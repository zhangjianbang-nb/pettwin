#!/usr/bin/env python3
"""PetTwin 版本发布脚本（VERSION 单一事实源）。

用法：
  python3 scripts/release.py check           # VERSION 与 CHANGELOG/git tag 一致性检查
  python3 scripts/release.py bump minor      # 0.3.0 -> 0.4.0（写 VERSION，CHANGELOG 需手动补条目）
  python3 scripts/release.py release         # 依据 VERSION 打 annotated tag v<x.y.z>（需工作区干净）
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_F = ROOT / "VERSION"
CHANGELOG_F = ROOT / "CHANGELOG.md"


def current():
    return VERSION_F.read_text().strip()


def changelog_has(v):
    return ("## [" + v + "]") in CHANGELOG_F.read_text()


def tag_exists(v):
    r = subprocess.run(["git", "tag", "-l", "v" + v], capture_output=True, text=True)
    return v in [t[1:] for t in r.stdout.split()]


def dirty_count():
    out = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    return len([ln for ln in out.splitlines() if ln.strip()])


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    v = current()
    if cmd == "check":
        ok = True
        if not changelog_has(v):
            print("CHANGELOG 缺", v, "条目")
            ok = False
        if tag_exists(v):
            print("tag v" + v + " 已存在")
            ok = False
        n = dirty_count()
        if n == 0:
            print("工作区干净")
        else:
            print("工作区不干净:", n, "个文件")
        print("VERSION:", v, "| CHANGELOG:", changelog_has(v), "| tag:", tag_exists(v))
        return 0 if ok else 1
    if cmd == "bump":
        part = sys.argv[2] if len(sys.argv) > 2 else "patch"
        major, minor, patch = (int(x) for x in v.split("."))
        vals = {"major": [major + 1, 0, 0], "minor": [major, minor + 1, 0], "patch": [major, minor, patch + 1]}
        nv = ".".join(str(x) for x in vals[part])
        VERSION_F.write_text(nv + "\n")
        print(v, "->", nv, "（记得补 CHANGELOG 条目）")
        return 0
    if cmd == "release":
        if not changelog_has(v):
            print("CHANGELOG 缺条目, 先补")
            return 1
        if dirty_count():
            print("工作区不干净, 先提交")
            return 1
        subprocess.run(["git", "tag", "-a", "v" + v, "-m", "PetTwin v" + v])
        print("tag v" + v + " 已打（git push origin v" + v + " 推送）")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
