#!/usr/bin/env python3
"""把品牌配置套到一棵一次性的源码树上，并审计有没有漏网的上游品牌。

为什么是脚本而不是直接改代码：上游「水杉」字样散在五百多个文件里，每月上万个提交。把改名提交进分支，
每次合并上游都要解一遍冲突；放在这里，分支上只有 brand/ 下的新文件，上游能一直干净地合进来，上游新加的
文案也会被同一套规则替换，漏掉的由 audit 报出来。

用法：

    python3 brand/apply.py plan                      # 只读：每条规则会改多少处
    python3 brand/apply.py apply --root <一次性树>    # 就地改写，只接受游离 HEAD 或 mocai/build* 分支上的干净树
    python3 brand/apply.py audit --root <已改写的树>  # 查漏网的上游品牌，有就以 1 退出
    python3 brand/apply.py audit --root <树> --release  # 再拒绝占位值，发版前必须通过

配置见 brand/mocai.toml，只依赖标准库（tomllib 要 Python 3.11+）。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import pathlib
import re
import subprocess
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "mocai.toml"
MARKER = "BRAND_APPLIED.json"
# 这些分支名之外一律不改写：品牌层分支要保持只含新文件，才能持续合并上游。
BUILD_BRANCH_PREFIX = "mocai/build"


class BrandError(Exception):
    """配置或源码树不满足前提；信息直接给人看。"""


def compile_glob(pattern: str) -> re.Pattern[str]:
    """把 `**` 风格的路径模式转成正则。不用 PurePath.full_match：它要 Python 3.13。"""
    out = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


def matches_any(path: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.match(path) for pattern in patterns)


def expand(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise BrandError(f"配置引用了未定义的值 ${{{key}}}")
        return values[key]

    return re.sub(r"\$\{([a-z0-9_]+)\}", replace, text)


@dataclass
class Rule:
    id: str
    pattern: re.Pattern[str]
    to: str
    include: list[re.Pattern[str]] = field(default_factory=list)
    exclude: list[re.Pattern[str]] = field(default_factory=list)

    def applies_to(self, path: str) -> bool:
        if self.include and not matches_any(path, self.include):
            return False
        return not matches_any(path, self.exclude)


@dataclass
class Patch:
    path: str
    find: str
    replace: str
    count: int


@dataclass
class AuditCheck:
    id: str
    pattern: re.Pattern[str]
    allow_line: re.Pattern[str] | None
    exclude: list[re.Pattern[str]]


@dataclass
class Config:
    values: dict[str, str]
    exclude: list[re.Pattern[str]]
    force_include: list[str]
    rules: list[Rule]
    patches: list[Patch]
    remove: list[str]
    post_commands: list[list[str]]
    audit: list[AuditCheck]
    review: list[AuditCheck]
    placeholder: list[re.Pattern[str]]


def load_config(path: pathlib.Path) -> Config:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    values = dict(raw["values"])
    # 下面三个值由配置推导，用在 re 的替换串里，改名时只改 identifier_root 和 short_zh 一处。
    # 契约文件里有写成正则的标识，JSON 里是 `app\\.msime\\.macos`；\g<1> 是匹配到的那串反斜杠，原样放回。
    values["identifier_root_regex"] = r"\g<1>.".join(values["identifier_root"].split("."))
    values["short_zh_unicode_escape"] = "".join(f"\\\\u{ord(char):04x}" for char in values["short_zh"])
    values["short_zh_percent"] = urllib.parse.quote(values["short_zh"], safe="")
    rules = []
    seen = set()
    for entry in raw["rules"]:
        if entry["id"] in seen:
            raise BrandError(f"规则 id 重复：{entry['id']}")
        seen.add(entry["id"])
        source = entry["regex"] if "regex" in entry else re.escape(entry["from"])
        rules.append(
            Rule(
                id=entry["id"],
                pattern=re.compile(source),
                # 替换串里的 \1 留给 re 处理，${name} 先展开。
                to=expand(entry["to"], values),
                include=[compile_glob(item) for item in entry.get("include", [])],
                exclude=[compile_glob(item) for item in entry.get("exclude", [])],
            )
        )
    patches = [
        Patch(
            path=entry["path"],
            find=expand(entry["find"], values),
            replace=expand(entry["replace"], values),
            count=entry.get("count", 1),
        )
        for entry in raw.get("patches", [])
    ]

    def checks(key: str) -> list[AuditCheck]:
        return [
            AuditCheck(
                id=entry["id"],
                pattern=re.compile(entry["regex"]),
                allow_line=re.compile(entry["allow_line"]) if "allow_line" in entry else None,
                exclude=[compile_glob(item) for item in entry.get("exclude", [])],
            )
            for entry in raw.get(key, [])
        ]

    return Config(
        values=values,
        exclude=[compile_glob(item) for item in raw["scope"]["exclude"]],
        force_include=raw["scope"].get("force_include", []),
        rules=rules,
        patches=patches,
        remove=raw.get("remove", {}).get("paths", []),
        post_commands=raw.get("post", {}).get("commands", []),
        audit=checks("audit"),
        review=checks("review"),
        placeholder=[re.compile(item) for item in raw["brand"]["placeholder_patterns"]],
    )


def git(root: pathlib.Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BrandError(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result.stdout


def tracked_files(root: pathlib.Path) -> list[str]:
    return [item for item in git(root, "ls-files", "-z").split("\0") if item]


def read_text(path: pathlib.Path) -> str | None:
    """二进制、符号链接和子模块一律不碰。按字节读写，换行和 BOM 原样保留。"""
    if path.is_symlink() or not path.is_file():
        return None
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scoped_files(root: pathlib.Path, config: Config):
    for relative in tracked_files(root):
        if matches_any(relative, config.exclude) and relative not in config.force_include:
            continue
        text = read_text(root / relative)
        if text is not None:
            yield relative, text


def apply_rules(relative: str, text: str, config: Config, counts: dict[str, int]) -> str:
    for rule in config.rules:
        if not rule.applies_to(relative):
            continue
        text, replaced = rule.pattern.subn(rule.to, text)
        if replaced:
            counts[rule.id] = counts.get(rule.id, 0) + replaced
    return text


def prepare(root: pathlib.Path, config: Config) -> tuple[dict[str, str], dict[str, int]]:
    """在内存里算出改写结果，不写盘。补丁锚点或待删文件对不上时抛错，上游动了这些地方就在这里暴露。"""
    rewritten: dict[str, str] = {}
    counts: dict[str, int] = {}
    for relative, text in scoped_files(root, config):
        new_text = apply_rules(relative, text, config, counts)
        if new_text != text:
            rewritten[relative] = new_text
    for patch in config.patches:
        current = rewritten.get(patch.path)
        if current is None:
            current = read_text(root / patch.path)
        if current is None:
            raise BrandError(f"补丁目标不存在或不是文本：{patch.path}")
        found = current.count(patch.find)
        if found != patch.count:
            raise BrandError(f"补丁锚点在 {patch.path} 出现 {found} 次，预期 {patch.count} 次，上游可能改了这里：{patch.find[:80]!r}")
        rewritten[patch.path] = current.replace(patch.find, patch.replace)
    for relative in config.remove:
        if not (root / relative).is_file():
            raise BrandError(f"要删除的文件不存在：{relative}")
    return rewritten, counts


def command_plan(root: pathlib.Path, config: Config) -> int:
    rewritten, counts = prepare(root, config)
    for rule in config.rules:
        print(f"{counts.get(rule.id, 0):>7}  {rule.id}")
    print(f"{len(rewritten):>7}  个文件会被改写")
    print(f"{len(config.patches):>7}  处定点补丁（锚点全部对得上），{len(config.remove)} 个文件会被删除")
    return 0


def require_disposable(root: pathlib.Path) -> None:
    if (root / MARKER).exists():
        raise BrandError(f"{root} 已经套过品牌（有 {MARKER}），规则不是幂等的，换一棵干净的树")
    if git(root, "status", "--porcelain").strip():
        raise BrandError(f"{root} 有未提交的改动，只改写干净的树")
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "HEAD" and not branch.startswith(BUILD_BRANCH_PREFIX):
        raise BrandError(f"{root} 在分支 {branch} 上；品牌层分支要保持干净，请用游离 HEAD 或 {BUILD_BRANCH_PREFIX}* 分支")


def command_apply(root: pathlib.Path, config: Config, config_path: pathlib.Path) -> int:
    require_disposable(root)
    # 先在内存里算完，锚点错了什么都不写，不留下改了一半的树。
    rewritten, counts = prepare(root, config)
    for relative, text in rewritten.items():
        if relative not in config.remove:
            (root / relative).write_bytes(text.encode("utf-8"))
    for relative in config.remove:
        (root / relative).unlink()
    # 生成文件里的名字是转义过的，文本规则替换不到，按改写后的版本表重新生成。
    for command in config.post_commands:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise BrandError(f"后处理命令失败：{' '.join(command)}\n{result.stdout}{result.stderr}")
    (root / MARKER).write_text(
        json.dumps(
            {
                "upstream_commit": git(root, "rev-parse", "HEAD").strip(),
                "config": config_path.name,
                "rewritten_files": len(rewritten),
                "rule_hits": counts,
                "removed": config.remove,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"已改写 {len(rewritten)} 个文件，删除 {len(config.remove)} 个文件")
    return 0


def run_checks(root: pathlib.Path, config: Config, checks: list[AuditCheck]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {check.id: [] for check in checks}
    for relative, text in scoped_files(root, config):
        for number, line in enumerate(text.splitlines(), 1):
            for check in checks:
                if not check.pattern.search(line):
                    continue
                if check.allow_line and check.allow_line.search(line):
                    continue
                if matches_any(relative, check.exclude):
                    continue
                hits[check.id].append(f"{relative}:{number}: {line.strip()[:120]}")
    return hits


def print_hits(hits: dict[str, list[str]], limit: int) -> None:
    for check_id, lines in hits.items():
        print(f"{len(lines):>7}  {check_id}")
        for line in lines[:limit]:
            print(f"         {line}")
        if len(lines) > limit:
            print(f"         ……还有 {len(lines) - limit} 处")


def command_audit(root: pathlib.Path, config: Config, release: bool, limit: int) -> int:
    failed = False
    if not (root / MARKER).exists():
        raise BrandError(f"{root} 还没有套品牌（没有 {MARKER}）")
    print("必须为零：")
    hits = run_checks(root, config, config.audit)
    print_hits(hits, limit)
    failed = any(hits.values())
    print("人工复核（不算失败）：")
    print_hits(run_checks(root, config, config.review), limit)
    if release:
        problems = [
            f"{key} = {value}"
            for key, value in config.values.items()
            if any(pattern.search(value) for pattern in config.placeholder)
        ]
        print(f"占位值：{len(problems)} 项")
        for problem in problems:
            print(f"         {problem}")
        failed = failed or bool(problems)
    print("审计结论：" + ("不通过" if failed else "通过"))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把品牌配置套到一次性源码树上并审计")
    parser.add_argument("command", choices=["plan", "apply", "audit"])
    parser.add_argument("--root", type=pathlib.Path, default=HERE.parent, help="要处理的 git 工作树")
    parser.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG)
    parser.add_argument("--release", action="store_true", help="audit 时再拒绝占位值")
    parser.add_argument("--limit", type=int, default=12, help="audit 每项最多列几条")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        config = load_config(args.config)
        if args.command == "plan":
            return command_plan(root, config)
        if args.command == "apply":
            return command_apply(root, config, args.config)
        return command_audit(root, config, args.release, args.limit)
    except BrandError as error:
        print(f"brand: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
