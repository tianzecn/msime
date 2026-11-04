#!/usr/bin/env python3
"""品牌层 brand/apply.py 的回归测试，以及 brand/mocai.toml 对当前源码树的预检。

合并上游之后最容易坏的是定点补丁：上游改了锚点那一行，发版时才发现就晚了。这里在未改写的树上把规则和补丁在内存里
跑一遍（不写盘），锚点对不上就失败。其余用例在临时 git 仓库里核对规则的边界：哪些字串要换、哪些绝不能换。
"""

from __future__ import annotations

import importlib.util
import pathlib
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
        self.assertEqual(rewrite("a.swift", "水杉账号"), "墨采账号")
        self.assertEqual(rewrite("a.plist", "Metasequoia IME"), "Mocai IME")
        self.assertEqual(rewrite("a.ts", "（MSIME，Metasequoia IME）"), "（Mocai IME）")

    def test_escaped_forms(self) -> None:
        self.assertEqual(rewrite("a.ts", r'"水杉"'), r'"墨采"')
        self.assertEqual(
            rewrite("shared/contracts/editions.schema.json", r'"^app\.msime\.macos(\.[a-z]+)?$"'),
            r'"^app\.mocai\.macos(\.[a-z]+)?$"',
        )
        # JSON 里的正则是两个反斜杠，反斜杠的数量要原样留着。
        self.assertEqual(rewrite("a.json", r'"^app\\.msime\\.macos$"'), r'"^app\\.mocai\\.macos$"')
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
        self.assertEqual(rewrite("a", "app.msime.macos"), "app.mocai.macos")
        self.assertEqual(rewrite("a", "group.app.msime.ios"), "group.app.mocai.ios")
        self.assertEqual(rewrite("a", "app.msime.inputmethod.MetasequoiaIME.Hans"), "app.mocai.inputmethod.MocaiIME.Hans")
        # Android 的 Java 包名和 JNI 符号也用它，只换字符串会编不过；这几个平台还不在首发范围。
        for text in ("app.msime.android", "app.msime.windows", "app.msime.linux", "app.msime.hmos"):
            self.assertEqual(rewrite("a", text), text)

    def test_services_never_point_at_a_real_placeholder_host(self) -> None:
        self.assertEqual(rewrite("a", "https://api.msime.app/v1"), "https://api.mocai.invalid/v1")
        self.assertEqual(rewrite("a", "media.msime.app"), "media.mocai.invalid")
        self.assertEqual(rewrite("a", "https://dl.msime.app/gh/"), "https://dl.mocai.invalid/gh/")
        self.assertEqual(rewrite("a", "https://msime.app/privacy/"), "https://mocai.invalid/privacy/")
        self.assertEqual(rewrite("a", "msime-tests.invalid"), "msime-tests.invalid")

    def test_pinned_resources_stay_upstream(self) -> None:
        # 资源包按 SHA-256 固定，继续下载上游发布；只有页面链接和更新检查换成墨采的。
        for text in (
            "https://github.com/metasequoiaime/msime/releases/download/voice-runtime/x.tar.gz",
            "https://github.com/metasequoiaime/msime-dictionary/releases/download/dict-v2/x.db",
            "https://github.com/metasequoiaime/chinese-ime-lm/releases/download/v1/x",
        ):
            self.assertEqual(rewrite("a", text), text)
        self.assertEqual(rewrite("a", "https://github.com/metasequoiaime/msime/issues"), "https://mocai.invalid/source/issues")
        # 检查更新解析 GitHub Releases 的 JSON，所以换成墨采自己的仓库，而不是自建后端的某个接口。
        self.assertEqual(
            rewrite("a", "https://api.github.com/repos/metasequoiaime/msime/releases?per_page=100"),
            "https://api.github.com/repos/mocai-placeholder/mocai/releases?per_page=100",
        )
        # appcast 也在 releases/download 下，但它不是固定资源，留着就会读水杉的更新源。
        self.assertEqual(
            rewrite("platforms/macos/package-release.sh", "https://github.com/metasequoiaime/msime/releases/download/macos-appcast/"),
            "https://github.com/mocai-placeholder/mocai/releases/download/macos-appcast/",
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

    def test_release_audit_rejects_placeholders(self) -> None:
        repository(self.root, {"a.txt": "ok\n"})
        detach(self.root)
        brand.command_apply(self.root, self.config, BRAND / "mocai.toml")
        self.assertEqual(brand.command_audit(self.root, self.config, False, 0), 0)
        self.assertEqual(brand.command_audit(self.root, self.config, True, 0), 1)


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
