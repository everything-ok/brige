"""
browser/profile.py 回归测试。

覆盖：metadata.json 损坏降级、原子写、路径穿越防护。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def pm(tmp_path):
    """每个用例独立的 ProfileManager（不碰真实 profiles 目录）"""
    from browser import profile as profile_mod

    real_dir = profile_mod.PROFILES_DIR
    profile_mod.PROFILES_DIR = tmp_path / "profiles"
    profile_mod.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        yield profile_mod.ProfileManager()
    finally:
        profile_mod.PROFILES_DIR = real_dir


def test_metadata_corrupted_does_not_crash(pm, tmp_path):
    """metadata.json 写坏了也不能让服务起不来"""
    pm.metadata_file.write_text("{ this is not json", encoding="utf-8")
    pm2 = pm.__class__()
    assert pm2._metadata == {"profiles": {}}
    # 损坏文件应被备份，不应直接消失
    assert pm2.metadata_file.with_suffix(".json.corrupt").exists()


def test_metadata_wrong_structure_resets(pm):
    """metadata.json 结构不对（缺 profiles）也应降级"""
    pm.metadata_file.write_text('{"foo": 1}', encoding="utf-8")
    pm2 = pm.__class__()
    assert pm2._metadata == {"profiles": {}}


def test_save_metadata_is_atomic_and_leaves_no_temp(pm):
    """原子写：不能留下半截文件或临时文件残留"""
    pm.set_login_status("poe", True, "ok")
    pm._save_metadata()
    data = json.loads(pm.metadata_file.read_text(encoding="utf-8"))
    assert data["profiles"]["poe"]["is_logged_in"] is True
    leftovers = [p.name for p in pm.profiles_dir.iterdir()
                 if p.name.startswith(".metadata-")]
    assert not leftovers, f"残留临时文件: {leftovers}"


def test_metadata_survives_repeated_writes(pm):
    """反复写不能把文件写坏"""
    for i in range(30):
        pm.mark_used("poe") if "poe" in pm._metadata["profiles"] \
            else pm.set_login_status("poe", True)
    data = json.loads(pm.metadata_file.read_text(encoding="utf-8"))
    assert "poe" in data["profiles"]


def test_profile_path_is_contained_in_profiles_dir(pm):
    """站点名含 ../ 时不能写到 profiles 目录之外（路径穿越）"""
    p = pm.get_profile_path("../../evil")
    resolved = p.resolve()
    base = pm.profiles_dir.resolve()
    assert str(resolved).startswith(str(base)), f"路径逃逸: {resolved}"


def test_profile_path_normal_name_unchanged(pm):
    """正常站点名不受影响"""
    p = pm.get_profile_path("poe")
    assert p.name == "poe"
    assert p.exists()


def test_clear_profile_contained(pm):
    """clear_profile 也不能删到 profiles 之外的文件"""
    outside = pm.profiles_dir.parent / "outside_keep.txt"
    outside.write_text("keep", encoding="utf-8")
    try:
        pm.get_profile_path("../outside_keep")
        pm.clear_profile("../outside_keep")
        assert outside.exists(), "clear_profile 删掉了 profiles 之外的文件"
    finally:
        if outside.exists():
            outside.unlink()


def test_login_status_roundtrip(pm):
    pm.set_login_status("poe", False, "session expired")
    assert pm.get_login_status("poe") is False
    assert pm.get_login_status("never-seen") is None
    pm.clear_profile("poe")
    assert pm.get_login_status("poe") is None
