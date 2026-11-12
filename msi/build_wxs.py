#!/usr/bin/env python
"""根据 msi/payload 的实际内容生成 msi/bridge.wxs（WiX v4/v5 语法）。

用法:
    python msi/build_wxs.py            # 生成 bridge.wxs
    python msi/build_wxs.py --check    # 仅校验已有 bridge.wxs 与 payload 是否一致

设计要点:
  * 每个源文件一个 Component；ComponentGuid 优先沿用历史 bridge.wxs 中的 GUID，
    保证升级链不破；新增文件用 UUIDv5 从路径确定性推导，跨机器构建结果一致。
  * 目录结构由 payload 实际目录自动推导。
  * 版本号统一在此维护（VERSION），与 Release tag 保持一致。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
OUT = ROOT / "bridge.wxs"

PACKAGE_NAME = "AI 模型桥接"
MANUFACTURER = "everything-ok"
VERSION = "1.1.0"
UPGRADE_CODE = "5C4A7B8E-1234-4D5E-9F6A-7B8C9D0E1F2A"
LANGUAGE = "2052"
NAMESPACE = uuid.UUID("6f1d5a3e-9c2b-4e77-8a44-2b0e9f7c1d55")

SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache"}
SKIP_EXT = {".pyc", ".pyo", ".wixpdb", ".msi"}
# 运行期误建目录（IDE 沙箱把 %USERPROFILE% 当字面量展开），非源码
SKIP_DIR_PREFIX = ("%USERPROFILE%", "%TEMP%", "AppData")

# 固定 ID（非文件组件）
MENU_SHORTCUT_GUID = "B2C3D4E5-2222-4B3C-9D4E-6F7A8B9C0D1E"
DESKTOP_SHORTCUT_GUID = "A1B2C3D4-1111-4A2B-8C3D-5E6F7A8B9C0D"


def list_payload() -> list[Path]:
    out: list[Path] = []
    for p in sorted(PAYLOAD.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(PAYLOAD)
        if any(part in SKIP_DIR_NAMES for part in rel.parts):
            continue
        if rel.parts and rel.parts[0].startswith(SKIP_DIR_PREFIX):
            continue
        if p.suffix.lower() in SKIP_EXT:
            continue
        out.append(p)
    return out


def historical_guids() -> dict[str, str]:
    """从 git 中的历史 bridge.wxs 提取 source -> componentGuid 映射。"""
    try:
        raw = subprocess.run(
            ["git", "show", "HEAD:msi/bridge.wxs"],
            cwd=ROOT.parent, capture_output=True, text=True, encoding="utf-8",
        )
    except Exception:
        return {}
    if raw.returncode != 0 or not raw.stdout:
        return {}
    text = raw.stdout
    mapping: dict[str, str] = {}
    pattern = re.compile(
        r'<Component\s+Id="[^"]+"\s+Guid="([^"]+)"[^>]*>\s*'
        r'<File\s+Id="[^"]+"\s+Source="([^"]+)"',
        re.S,
    )
    for guid, source in pattern.findall(text):
        if source.startswith("payload/"):
            mapping[source[len("payload/"):]] = guid
    return mapping


def det_guid(rel: str, hist: dict[str, str]) -> str:
    if rel in hist:
        return hist[rel]
    return str(uuid.uuid5(NAMESPACE, "component|" + rel))


def det_id(prefix: str, rel: str) -> str:
    h = uuid.uuid5(NAMESPACE, prefix + "|" + rel).hex.upper()
    return f"{prefix}{h[:16]}"


def dir_id(rel: str) -> str:
    """rel 为相对 payload 的目录，'.' 表示顶层（映射到 INSTALLFOLDER）。"""
    if rel == ".":
        return "INSTALLFOLDER"
    return det_id("Dir", rel)


def build() -> str:
    files = list_payload()
    hist = historical_guids()

    # dir_key（相对 payload）-> 该目录下的直属文件列表
    dirs: dict[str, list[str]] = {}
    all_dirs: set[str] = {"."}
    for p in files:
        rel = str(p.relative_to(PAYLOAD)).replace("\\", "/")
        parent = str(Path(rel).parent).replace("\\", "/")
        dirs.setdefault(parent, []).append(rel)
        # 记录所有祖先目录
        parts = Path(rel).parts
        for i in range(1, len(parts)):
            all_dirs.add("/".join(parts[:i]))

    lines: list[str] = []
    lines.append('<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">')
    lines.append(
        f'  <Package Name="{PACKAGE_NAME}" Manufacturer="{MANUFACTURER}" Version="{VERSION}"\n'
        f'           UpgradeCode="{UPGRADE_CODE}" Language="{LANGUAGE}"\n'
        f'           Scope="perUser" Codepage="65001">'
    )
    lines.append('    <SummaryInformation Description="AI 模型桥接：网页模型 → 本地 API 网关" />')
    lines.append('    <MajorUpgrade DowngradeErrorMessage="已安装更新版本 [ProductVersion]，如需重装请先卸载。" />')
    lines.append('    <MediaTemplate EmbedCab="yes" />')
    lines.append('    <Icon Id="bridge.ico" SourceFile="payload/ai-bridge/bridge.ico" />')
    lines.append('    <Property Id="ARPPRODUCTICON" Value="bridge.ico" />')
    lines.append('    <StandardDirectory Id="LocalAppDataFolder">')
    lines.append('      <Directory Id="INSTALLFOLDER" Name="AI 模型桥接">')

    def emit_dir(parent: str, depth: int) -> None:
        pad = "  " * depth
        children = sorted(dirs.get(parent, []))
        prefix = "" if parent == "." else parent + "/"
        subdirs = {
            d for d in all_dirs
            if d != parent and d.startswith(prefix) and "/" not in d[len(prefix):]
        }
        for d in sorted(subdirs):
            name = d.rsplit("/", 1)[-1]
            lines.append(f'{pad}<Directory Id="{dir_id(d)}" Name="{name}">')
            emit_dir(d, depth + 1)
            lines.append(f"{pad}</Directory>")
        for f in children:
            if str(Path(f).parent).replace("\\", "/") != parent:
                continue
            cid = det_id("cmp", f)
            fid = det_id("fil", f)
            guid = det_guid(f, hist)
            lines.append(f'{pad}  <Component Id="{cid}" Guid="{guid}" Directory="{dir_id(parent)}">')
            lines.append(f'{pad}    <File Id="{fid}" Source="payload/{f}" KeyPath="yes" />')
            lines.append(f"{pad}  </Component>")

    emit_dir(".", 3)

    lines.append("      </Directory>")
    lines.append("    </StandardDirectory>")
    lines.append('    <StandardDirectory Id="ProgramMenuFolder">')
    lines.append('      <Directory Id="AppMenuDir" Name="AI 模型桥接">')
    lines.append(f'        <Component Id="MenuShortcut" Guid="{MENU_SHORTCUT_GUID}">')
    lines.append('          <Shortcut Id="MenuExeShortcut" Name="AI 模型桥接" Description="启动 AI 模型桥接服务并打开管理页"')
    lines.append('                    Target="[INSTALLFOLDER]AI模型桥接.exe" WorkingDirectory="INSTALLFOLDER"')
    lines.append('                    Icon="bridge.ico" />')
    lines.append('          <RegistryValue Root="HKCU" Key="Software/AI模型桥接" Name="menu_shortcut" Value="1" Type="integer" KeyPath="yes" />')
    lines.append("        </Component>")
    lines.append("      </Directory>")
    lines.append("    </StandardDirectory>")
    lines.append('    <StandardDirectory Id="DesktopFolder">')
    lines.append(f'      <Component Id="DesktopShortcut" Guid="{DESKTOP_SHORTCUT_GUID}">')
    lines.append('        <Shortcut Id="DesktopExeShortcut" Name="AI 模型桥接" Description="启动 AI 模型桥接服务并打开管理页"')
    lines.append('                Target="[INSTALLFOLDER]AI模型桥接.exe" WorkingDirectory="INSTALLFOLDER"')
    lines.append('                Icon="bridge.ico" />')
    lines.append('        <RegistryValue Root="HKCU" Key="Software/AI模型桥接" Name="desktop_shortcut" Value="1" Type="integer" KeyPath="yes" />')
    lines.append("      </Component>")
    lines.append("    </StandardDirectory>")
    lines.append('    <Feature Id="Main" Level="1" Title="AI 模型桥接核心文件">')
    lines.append('      <ComponentRef Id="MenuShortcut" />')
    lines.append('      <ComponentRef Id="DesktopShortcut" />')
    for p in files:
        rel = str(p.relative_to(PAYLOAD)).replace("\\", "/")
        lines.append(f'      <ComponentRef Id="{det_id("cmp", rel)}" />')
    lines.append("    </Feature>")
    lines.append("  </Package>")
    lines.append("</Wix>")
    return "\n".join(lines) + "\n"


def check() -> int:
    if not OUT.is_file():
        print("bridge.wxs 不存在", file=sys.stderr)
        return 1
    src = OUT.read_text(encoding="utf-8")
    referenced = {
        r[len("payload/"):]
        for r in re.findall(r'Source="([^"]+)"', src)
        if r.startswith("payload/")
    }
    actual = {
        str(p.relative_to(PAYLOAD)).replace("\\", "/")
        for p in list_payload()
    }
    missing = sorted(actual - referenced)
    extra = sorted(referenced - actual)
    ok = True
    if missing:
        ok = False
        print("payload 中有文件未被 wxs 收录:")
        for m in missing:
            print("  +", m)
    if extra:
        ok = False
        print("wxs 引用了 payload 中不存在的文件:")
        for e in extra:
            print("  -", e)
    if ok:
        print(f"OK: wxs 与 payload 一致（{len(actual)} 个文件）")
        return 0
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    OUT.write_text(build(), encoding="utf-8")
    print(f"已生成 {OUT}（{len(list_payload())} 个文件，版本 {VERSION}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
