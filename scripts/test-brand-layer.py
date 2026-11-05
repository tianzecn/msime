#!/usr/bin/env python3
"""品牌层 brand/apply.py 的回归测试，以及 brand/mocai.toml 对当前源码树的预检。

合并上游之后最容易坏的是定点补丁：上游改了锚点那一行，发版时才发现就晚了。这里在未改写的树上把规则和补丁在内存里
跑一遍（不写盘），锚点对不上就失败。其余用例在临时 git 仓库里核对规则的边界：哪些字串要换、哪些绝不能换。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRAND = ROOT / "brand"

if not (BRAND / "apply.py").is_file():
    print("skipped: 没有 brand/apply.py（这是上游的树）")
    sys.exit(0)
if sys.version_info < (3, 11):
    print("skipped: brand/apply.py 要 Python 3.11+（tomllib）")
    sys.exit(0)

spec = importlib.util.spec_from_file_location("brand_apply", BRAND / "apply.py")
assert spec and spec.loader
brand = importlib.util.module_from_spec(spec)
sys.modules["brand_apply"] = brand
spec.loader.exec_module(brand)

CONFIG = brand.load_config(BRAND / "mocai.toml")
# 期望值从配置读，改域名、仓库或标识时测试不用跟着改。
V = CONFIG.values
ID = V["identifier_root"]
REPO = V["release_repo"]


def rewrite(relative: str, text: str) -> str:
    return brand.apply_rules(relative, text, CONFIG, {})


def repository(directory: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "brand-test@example.invalid")
    git("config", "user.name", "brand-test")
    for relative, text in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "fixture")
    return directory


def detach(directory: pathlib.Path) -> None:
    subprocess.run(["git", "-C", str(directory), "checkout", "-q", "--detach"], check=True, capture_output=True)


class Rules(unittest.TestCase):
    def test_display_names(self) -> None:
        self.assertEqual(rewrite("a.swift", "水杉输入法"), "墨采输入法")
        # 吉祥物换成了墨墨，不是「墨采娘」
        self.assertEqual(rewrite("a.tsx", "水杉娘插画"), "墨墨插画")
        self.assertEqual(rewrite("a.swift", "水杉账号"), "墨采账号")
        self.assertEqual(rewrite("a.plist", "Metasequoia IME"), "Mocai IME")
        self.assertEqual(rewrite("a.ts", "（MSIME，Metasequoia IME）"), "（Mocai IME）")
        self.assertEqual(rewrite("a.strings", '"x.Hans" = "Metasequoia Pinyin";'), '"x.Hans" = "Mocai Pinyin";')
        # 只改模式名：类名和版权行不动。
        self.assertEqual(rewrite("a.mm", "MetasequoiaCandidatePanel"), "MetasequoiaCandidatePanel")

    def test_escaped_forms(self) -> None:
        self.assertEqual(rewrite("a.ts", r'"水杉"'), r'"墨采"')
        self.assertEqual(
            rewrite("shared/contracts/editions.schema.json", r'"^app\.msime\.macos(\.[a-z]+)?$"'),
            '"^' + ID.replace(".", "\\.") + r'\.macos(\.[a-z]+)?$"',
        )
        # JSON 里的正则是两个反斜杠，反斜杠的数量要原样留着。
        self.assertEqual(rewrite("a.json", r'"^app\\.msime\\.macos$"'), '"^' + ID.replace(".", "\\\\.") + r'\\.macos$"')
        android = r'"^app\\.msime\\.android(\\.[a-z]+)?$"'
        self.assertEqual(rewrite("a.json", android), android)
        self.assertEqual(rewrite("a.cpp", "%E6%B0%B4%E6%9D%89+IME"), "%E5%A2%A8%E9%87%87+IME")

    def test_data_directories_do_not_collide_with_upstream(self) -> None:
        # 共用目录时，墨采的崩溃记录会进水杉的上报队列。这条曾漏掉 msime-mcp。
        path = "crates/mcp-server/src/crash_reporting.rs"
        self.assertEqual(
            rewrite(path, 'telemetry_support_directory()?.join("MSIME");'),
            'telemetry_support_directory()?.join("Mocai");',
        )
        self.assertEqual(rewrite(path, 'join("Library/Application Support/MSIME/telemetry")'), 'join("Library/Application Support/Mocai/telemetry")')
        self.assertEqual(rewrite("platforms/macos/src/core/EditionIdentity.h", '@"MSIME/%@/telemetry"'), '@"Mocai/%@/telemetry"')
        # 移动端的相对路径在各自的 App Group 容器里，不会撞车，也不在这条规则的范围内。
        mobile = 'root.join("MSIME").join("clipboard_history.json")'
        self.assertEqual(rewrite("crates/host-api/src/ffi/host.rs", mobile), mobile)

    def test_internal_names_stay(self) -> None:
        # 类名、C 符号和 crate 名用户看不到，改了只会让每次合并上游都冲突。
        for text in ("MSIMEInputController", "msime_client_create", "msime-host-api", "MSIME_EDITION_FULL"):
            self.assertEqual(rewrite("a.mm", text), text)

    def test_identifiers_only_for_shipping_platforms(self) -> None:
        self.assertEqual(rewrite("a", "app.msime.macos"), f"{ID}.macos")
        self.assertEqual(rewrite("a", "group.app.msime.ios"), f"group.{ID}.ios")
        self.assertEqual(rewrite("a", "app.msime.inputmethod.MetasequoiaIME.Hans"), f"{ID}.inputmethod.MocaiIME.Hans")
        # Android 的 Java 包名和 JNI 符号也用它，只换字符串会编不过；这几个平台还不在首发范围。
        for text in ("app.msime.android", "app.msime.windows", "app.msime.linux", "app.msime.hmos"):
            self.assertEqual(rewrite("a", text), text)

    def test_services_point_at_mocai(self) -> None:
        self.assertEqual(rewrite("a", "https://api.msime.app/v1"), f"https://{V['api_host']}/v1")
        self.assertEqual(rewrite("a", "media.msime.app"), V["media_host"])
        self.assertEqual(rewrite("a", "https://dl.msime.app/gh/"), f"https://{V['download_host']}/gh/")
        self.assertEqual(rewrite("a", "https://msime.app/privacy/"), f"https://{V['site_host']}/privacy/")
        self.assertEqual(rewrite("a", "msime-tests.invalid"), "msime-tests.invalid")

    def test_pinned_resources_stay_upstream(self) -> None:
        # 资源包按 SHA-256 固定，继续下载上游发布；只有页面链接和更新检查换成墨采的。
        for text in (
            "https://github.com/metasequoiaime/msime/releases/download/voice-runtime/x.tar.gz",
            "https://github.com/metasequoiaime/msime-dictionary/releases/download/dict-v2/x.db",
            "https://github.com/metasequoiaime/chinese-ime-lm/releases/download/v1/x",
        ):
            self.assertEqual(rewrite("a", text), text)
        self.assertEqual(rewrite("a", "https://github.com/metasequoiaime/msime/issues"), f"{V['source_url']}/issues")
        # 墨采自己的发版产物换成墨采的仓库，前后要成套，否则发版脚本对不上。
        for tag in ("macos-v1.2.0/x.zip", "nightly/x.pkg", "$release/"):
            self.assertEqual(
                rewrite("platforms/macos/scripts/x.sh", f"https://github.com/metasequoiaime/msime/releases/download/{tag}"),
                f"https://github.com/{REPO}/releases/download/{tag}",
            )
        # 检查更新解析 GitHub Releases 的 JSON，所以换成墨采自己的仓库，而不是自建后端的某个接口。
        self.assertEqual(
            rewrite("a", "https://api.github.com/repos/metasequoiaime/msime/releases?per_page=100"),
            f"https://api.github.com/repos/{REPO}/releases?per_page=100",
        )
        # appcast 也在 releases/download 下，但它不是固定资源，留着就会读水杉的更新源。
        self.assertEqual(
            rewrite("platforms/macos/package-release.sh", "https://github.com/metasequoiaime/msime/releases/download/macos-appcast/"),
            f"https://github.com/{REPO}/releases/download/macos-appcast/",
        )

    def test_notices_are_out_of_scope(self) -> None:
        for path in (
            "LICENSE",
            "crates/engine/LICENSE",
            "resources/licenses/rime-wanxiang-CC-BY-4.0.txt",
            "platforms/macos/resources/Licenses/x.txt",
            "resources/helpcodes/NOTICE.md",
            "platforms/ios/SharedResources/MLKit-NOTICES.txt",
            "plugins/script/uuid/plugin.toml",
            "crates/engine/Cargo.toml",
            "Cargo.lock",
        ):
            self.assertTrue(brand.matches_any(path, CONFIG.exclude), path)


class Tree(unittest.TestCase):
    def setUp(self) -> None:
        self.holder = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.holder.name)
        self.config = brand.load_config(BRAND / "mocai.toml")
        # 临时仓库里没有这些文件和生成器。
        self.config.patches = []
        self.config.remove = []
        self.config.post_commands = []
        self.config.assets_root = None

    def tearDown(self) -> None:
        self.holder.cleanup()

    def test_refuses_a_named_branch(self) -> None:
        repository(self.root, {"a.txt": "水杉"})
        with self.assertRaisesRegex(brand.BrandError, "分支"):
            brand.command_apply(self.root, self.config, BRAND / "mocai.toml")
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "水杉")

    def test_refuses_a_dirty_tree_and_a_second_run(self) -> None:
        repository(self.root, {"a.txt": "水杉"})
        detach(self.root)
        (self.root / "a.txt").write_text("改动", encoding="utf-8")
        with self.assertRaisesRegex(brand.BrandError, "未提交"):
            brand.command_apply(self.root, self.config, BRAND / "mocai.toml")
        subprocess.run(["git", "-C", str(self.root), "checkout", "-q", "--", "a.txt"], check=True)
        self.assertEqual(brand.command_apply(self.root, self.config, BRAND / "mocai.toml"), 0)
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "墨采")
        with self.assertRaisesRegex(brand.BrandError, "已经套过"):
            brand.command_apply(self.root, self.config, BRAND / "mocai.toml")

    def test_a_moved_anchor_writes_nothing(self) -> None:
        repository(self.root, {"a.txt": "水杉", "b.txt": "别的内容"})
        detach(self.root)
        self.config.patches = [brand.Patch(path="b.txt", find="上游已经改掉的锚点", replace="x", count=1)]
        with self.assertRaisesRegex(brand.BrandError, "锚点"):
            brand.command_apply(self.root, self.config, BRAND / "mocai.toml")
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "水杉")

    def test_binary_and_line_endings_survive(self) -> None:
        repository(self.root, {"a.txt": "水杉\r\n"})
        (self.root / "b.bin").write_bytes(b"\0\xff" + "水杉".encode())
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "bin"], check=True)
        detach(self.root)
        brand.command_apply(self.root, self.config, BRAND / "mocai.toml")
        self.assertEqual((self.root / "a.txt").read_bytes(), "墨采\r\n".encode())
        self.assertIn(b"\0", (self.root / "b.bin").read_bytes())

    def test_audit_finds_leftovers_and_allows_the_gpl_notice(self) -> None:
        repository(self.root, {"a.txt": "ok\n"})
        detach(self.root)
        brand.command_apply(self.root, self.config, BRAND / "mocai.toml")
        allowed = "墨采输入法基于水杉输入法（MSIME）修改，按 GPL-3.0 发布\n"
        (self.root / "b.txt").write_text(allowed, encoding="utf-8")
        (self.root / "c.txt").write_text("回到水杉\nhttps://api.msime.app\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        hits = brand.run_checks(self.root, self.config, self.config.audit)
        self.assertEqual(len(hits["水杉字样"]), 1)
        self.assertIn("c.txt", hits["水杉字样"][0])
        self.assertEqual(len(hits["上游服务主机"]), 1)

    def test_assets_replace_upstream_bytes_and_need_a_target(self) -> None:
        repository(self.root, {"art/icon.svg": "<svg>水杉</svg>\n"})
        (self.root / "art/logo.png").write_bytes(b"\x89PNG upstream")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "png"], check=True)
        detach(self.root)
        assets = self.root.parent / f"{self.root.name}-assets"
        (assets / "art").mkdir(parents=True)
        (assets / "art/logo.png").write_bytes(b"\x89PNG mocai")
        (assets / "art/missing.png").write_bytes(b"x")
        self.config.assets_root = assets
        try:
            with self.assertRaisesRegex(brand.BrandError, "missing.png"):
                brand.command_apply(self.root, self.config, BRAND / "mocai.toml")
            self.assertEqual((self.root / "art/logo.png").read_bytes(), b"\x89PNG upstream")
            (assets / "art/missing.png").unlink()
            brand.command_apply(self.root, self.config, BRAND / "mocai.toml")
            self.assertEqual((self.root / "art/logo.png").read_bytes(), b"\x89PNG mocai")
            self.assertEqual((self.root / "art/icon.svg").read_text(encoding="utf-8"), "<svg>墨采</svg>\n")
        finally:
            shutil.rmtree(assets)

    def test_release_audit_rejects_placeholders(self) -> None:
        repository(self.root, {"a.txt": "ok\n"})
        detach(self.root)
        brand.command_apply(self.root, self.config, BRAND / "mocai.toml")
        self.assertEqual(brand.command_audit(self.root, self.config, False, 0), 0)
        self.assertEqual(brand.command_audit(self.root, self.config, True, 0), 1)


class Artwork(unittest.TestCase):
    """代码里画的标志由补丁换成 brand/art/generate.py 的笔画，两边要是同一组坐标。"""

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("brand_art", BRAND / "art/generate.py")
        cls.art = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.art)
        cls.replaced = "\n".join(patch.replace for patch in CONFIG.patches)

    def test_code_drawn_marks_use_the_generated_stroke(self) -> None:
        for text in (self.art.STROKE_D, self.art.STROKE_D_132, self.art.swift_stroke_body(), self.art.appkit_stroke_body()):
            self.assertIn(text, self.replaced)

    def test_code_drawn_marks_use_the_generated_colours(self) -> None:
        for colour in (self.art.FIELD, self.art.EDGE):
            self.assertIn(colour, self.replaced)
            self.assertIn(self.art.hex_channels(colour, "0x{r} / 255.0, green: 0x{g} / 255.0, blue: 0x{b} / 255.0"), self.replaced)

    def test_every_sticker_has_a_file(self) -> None:
        stickers = CONFIG.assets_root / "resources/stickers/shuishan"
        manifest = json.loads((stickers / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["stickers"]), len(self.art.STICKERS))
        for entry in manifest["stickers"]:
            self.assertTrue((stickers / entry["file"]).is_file(), entry)


class CurrentTree(unittest.TestCase):
    def test_patches_still_fit_this_tree(self) -> None:
        if (ROOT / brand.MARKER).exists():
            self.skipTest("这棵树已经套过品牌")
        rewritten, counts = brand.prepare(ROOT, CONFIG)
        self.assertGreater(len(rewritten), 0)
        unused = [rule.id for rule in CONFIG.rules if not counts.get(rule.id)]
        self.assertEqual(unused, [], "这些规则一处都没命中，上游可能改了写法")


if __name__ == "__main__":
    unittest.main(verbosity=1)
