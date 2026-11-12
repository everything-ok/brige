#!/usr/bin/env python
"""把 ai-bridge / free-gateway 的最新源码同步到 msi/payload/。

用法:
    python msi/sync_payload.py            # 同步并报告
    python msi/sync_payload.py --check    # 仅检查是否一致（CI 友好）

排除规则:
  * 缓存与构建产物目录：__pycache__ / .pytest_cache / node_modules / profiles / logs
  * 二进制与缓存扩展名：.pyc / .pyo / .wixpdb / .msi
  * 私有配置文件：.env（含真实 API Key，绝不能进安装包）
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "msi" / "payload"
SOURCES = ("ai-bridge", "free-gateway")

SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", "node_modules", "profiles", "logs"}
SKIP_EXT = {".pyc", ".pyo", ".wixpdb", ".msi"}
SKIP_NAMES = {".env"}
# 运行期误建目录（IDE 沙箱把 %USERPROFILE% 当字面量展开），非源码
SKIP_DIR_PREFIX = ("%USERPROFILE%", "%TEMP%", "AppData")


def iter_source_files(src_root: Path):
    for p in sorted(src_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src_root)
        if any(part in SKIP_DIR_NAMES for part in rel.parts):
            continue
        if rel.parts and rel.parts[0].startswith(SKIP_DIR_PREFIX):
            continue
        if p.suffix.lower() in SKIP_EXT:
            continue
        if p.name in SKIP_NAMES:
            continue
        yield p, rel


def sync() -> tuple[int, list[str]]:
    copied = 0
    changed: list[str] = []
    for name in SOURCES:
        src_root = ROOT / name
        if not src_root.is_dir():
            print(f"跳过：{src_root} 不存在", file=sys.stderr)
            continue
        dst_root = PAYLOAD / name
        dst_root.mkdir(parents=True, exist_ok=True)
        for p, rel in iter_source_files(src_root):
            dst = dst_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or not filecmp.cmp(p, dst, shallow=False):
                shutil.copy2(p, dst)
                copied += 1
                changed.append(f"{name}/{rel.as_posix()}")
    return copied, changed


def check() -> int:
    problems: list[str] = []
    for name in SOURCES:
        src_root = ROOT / name
        if not src_root.is_dir():
            continue
        dst_root = PAYLOAD / name
        for p, rel in iter_source_files(src_root):
            dst = dst_root / rel
            if not dst.exists():
                problems.append(f"缺失: {name}/{rel.as_posix()}")
            elif not filecmp.cmp(p, dst, shallow=False):
                problems.append(f"不同: {name}/{rel.as_posix()}")
    if problems:
        print("payload 与源码不一致:")
        for x in problems:
            print("  " + x)
        print("\n运行 `python msi/sync_payload.py` 修复。")
        return 2
    print("OK: msi/payload 与源码一致")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    copied, changed = sync()
    print(f"已同步 {copied} 个文件到 msi/payload")
    for c in changed[:40]:
        print("  *", c)
    if len(changed) > 40:
        print(f"  ... 另有 {len(changed) - 40} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
