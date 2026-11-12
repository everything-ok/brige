import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime
from rich.console import Console

console = Console()

PROFILES_DIR = Path(__file__).parent.parent / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)

# 站点名只允许安全字符 —— site_name 会被直接用作目录名，
# 不限制就可能通过 "../" 写到 profiles 之外（路径穿越）。
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _safe_dirname(site_name: str) -> str:
    """把站点名转成安全的目录名；非法字符一律替换掉"""
    name = _SAFE_NAME_RE.match(site_name or "")
    if name:
        return site_name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", site_name or "")[:64]
    cleaned = cleaned.lstrip(".") or "unknown"
    return cleaned


class ProfileManager:
    """浏览器 Profile 管理"""

    def __init__(self):
        self.profiles_dir = PROFILES_DIR
        self.metadata_file = self.profiles_dir / "metadata.json"
        self._metadata = self._load_metadata()

    def _load_metadata(self) -> Dict:
        """读取 metadata.json

        文件损坏（写盘中断 / 磁盘满 / 手工改坏）时不能让整个服务起不来 ——
        降级为空数据并备份损坏文件，让服务还能启动。
        """
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "profiles" in data:
                    return data
                console.print(f"[yellow]⚠️ metadata.json 结构异常，已重置: "
                              f"{self.metadata_file}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]⚠️ metadata.json 读取失败({e})，已备份并重置[/yellow]")
                try:
                    self.metadata_file.replace(
                        self.metadata_file.with_suffix(".json.corrupt"))
                except Exception:
                    pass
        return {"profiles": {}}

    def _save_metadata(self):
        """原子写 metadata.json

        先写临时文件再 os.replace —— 避免写到一半被中断
        留下半截 JSON，下次启动直接读不出来。
        """
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self.profiles_dir),
                                       prefix=".metadata-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._metadata, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.metadata_file)
            except Exception:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                raise
        except Exception as e:
            console.print(f"[yellow]⚠️ metadata.json 写入失败: {e}[/yellow]")

    def get_profile_path(self, site_name: str) -> Path:
        """获取站点专用 Profile 路径"""
        profile_path = self.profiles_dir / _safe_dirname(site_name)
        profile_path.mkdir(parents=True, exist_ok=True)

        if site_name not in self._metadata["profiles"]:
            self._metadata["profiles"][site_name] = {
                "created": datetime.now().isoformat(),
                "last_used": None,
                "use_count": 0,
            }
            self._save_metadata()

        return profile_path

    def mark_used(self, site_name: str):
        """标记 Profile 被使用"""
        if site_name in self._metadata["profiles"]:
            self._metadata["profiles"][site_name]["last_used"] = datetime.now().isoformat()
            self._metadata["profiles"][site_name]["use_count"] += 1
            self._save_metadata()

    def set_login_status(self, site_name: str, is_logged_in: bool, note: str = ""):
        """设置站点的登录状态"""
        if site_name not in self._metadata["profiles"]:
            self._metadata["profiles"][site_name] = {
                "created": datetime.now().isoformat(),
                "last_used": None,
                "use_count": 0,
            }
        self._metadata["profiles"][site_name]["is_logged_in"] = is_logged_in
        self._metadata["profiles"][site_name]["login_check_time"] = datetime.now().isoformat()
        if note:
            self._metadata["profiles"][site_name]["login_note"] = note
        self._save_metadata()

    def get_login_status(self, site_name: str) -> Optional[bool]:
        """获取站点的登录状态，None 表示未知"""
        profile = self._metadata.get("profiles", {}).get(site_name)
        if profile:
            return profile.get("is_logged_in")
        return None

    def has_profile(self, site_name: str) -> bool:
        """检查是否存在该站点的 Profile 目录"""
        profile_path = self.profiles_dir / _safe_dirname(site_name)
        return profile_path.exists() and any(profile_path.iterdir())

    def clear_profile(self, site_name: str):
        """清除站点 Profile（登录态失效时）"""
        profile_path = self.profiles_dir / _safe_dirname(site_name)
        if profile_path.exists():
            shutil.rmtree(profile_path, ignore_errors=True)
            console.print(f"[yellow]🗑️  已清除 {site_name} 的 Profile[/yellow]")

        if site_name in self._metadata["profiles"]:
            del self._metadata["profiles"][site_name]
            self._save_metadata()

    def list_profiles(self) -> Dict:
        """列出所有 Profile 状态"""
        return self._metadata.get("profiles", {})

    def get_all_login_status(self) -> Dict[str, Dict]:
        """获取所有站点的登录状态摘要"""
        result = {}
        profiles = self._metadata.get("profiles", {})
        for site_name in profiles:
            profile = profiles[site_name]
            result[site_name] = {
                "has_profile": self.has_profile(site_name),
                "is_logged_in": profile.get("is_logged_in"),
                "last_used": profile.get("last_used"),
                "login_check_time": profile.get("login_check_time"),
            }
        return result


# 全局单例
profile_manager = ProfileManager()
