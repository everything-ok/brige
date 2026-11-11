"""
测试配置加载模块
"""
import sys
import pytest
import tempfile
import yaml
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class TestConfigDataclasses:
    """测试配置数据类"""

    def test_rate_limit_defaults(self):
        from config import RateLimitConfig
        cfg = RateLimitConfig()
        assert cfg.requests_per_minute == 2
        assert cfg.requests_per_hour == 30
        assert cfg.requests_per_day == 200
        assert cfg.min_interval_seconds == 25
        assert cfg.max_interval_seconds == 120

    def test_behavior_defaults(self):
        from config import BehaviorConfig
        cfg = BehaviorConfig()
        assert cfg.typing_speed_min_ms == 40
        assert cfg.typing_speed_max_ms == 180
        assert 0 <= cfg.typing_mistake_rate <= 1
        assert 0 <= cfg.scroll_probability <= 1

    def test_cooldown_defaults(self):
        from config import CooldownConfig
        cfg = CooldownConfig()
        assert cfg.initial_seconds == 300
        assert cfg.max_seconds == 3600
        assert cfg.multiplier == 2.0
        assert cfg.reset_after_success == 3

    def test_security_defaults(self):
        from config import SecurityConfig
        cfg = SecurityConfig()
        assert cfg.auto_pause is True
        assert cfg.max_wait_minutes == 30

    def test_site_config_creation(self):
        from config import SiteConfig
        site = SiteConfig(name="test", url="https://example.com")
        assert site.name == "test"
        assert site.url == "https://example.com"
        assert site.enabled is True
        assert site.priority == 10
        assert site.selectors == {}
        assert site.models == {}
        assert site.rate_limit is None


class TestConfigYamlLoading:
    """测试 YAML 配置加载"""

    def _make_config_with_yaml(self, yaml_content: str):
        """用临时 YAML 文件创建 Config 实例"""
        from config import Config, RateLimitConfig, BehaviorConfig, CooldownConfig, SecurityConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "sites.yaml"
            yaml_path.write_text(yaml_content, encoding="utf-8")

            # 临时替换 BASE_DIR
            import config as cfg_module
            original_base = cfg_module.BASE_DIR
            cfg_module.BASE_DIR = Path(tmpdir)
            (Path(tmpdir) / "profiles").mkdir()
            (Path(tmpdir) / "logs").mkdir()

            try:
                c = Config.__new__(Config)
                c.global_rate_limit = RateLimitConfig()
                c.behavior = BehaviorConfig()
                c.cooldown = CooldownConfig()
                c.security = SecurityConfig()
                c.sites = []
                c.captcha_indicators = []
                c._load_yaml()
                return c
            finally:
                cfg_module.BASE_DIR = original_base

    def test_load_single_site(self):
        yaml_content = """
sites:
  - name: testsite
    enabled: true
    url: "https://test.example.com"
    priority: 1
    models:
      gpt-4o: "GPT-4o"
"""
        c = self._make_config_with_yaml(yaml_content)
        assert len(c.sites) == 1
        assert c.sites[0].name == "testsite"
        assert c.sites[0].url == "https://test.example.com"
        assert c.sites[0].priority == 1
        assert "gpt-4o" in c.sites[0].models

    def test_disabled_site_excluded(self):
        yaml_content = """
sites:
  - name: active
    enabled: true
    url: "https://active.example.com"
  - name: inactive
    enabled: false
    url: "https://inactive.example.com"
"""
        c = self._make_config_with_yaml(yaml_content)
        # disabled 站点不加载
        assert len(c.sites) == 1
        assert c.sites[0].name == "active"

    def test_sites_sorted_by_priority(self):
        yaml_content = """
sites:
  - name: low
    enabled: true
    url: "https://low.example.com"
    priority: 5
  - name: high
    enabled: true
    url: "https://high.example.com"
    priority: 1
"""
        c = self._make_config_with_yaml(yaml_content)
        assert c.sites[0].name == "high"
        assert c.sites[1].name == "low"

    def test_site_with_custom_rate_limit(self):
        yaml_content = """
sites:
  - name: custom
    enabled: true
    url: "https://custom.example.com"
    rate_limit:
      requests_per_hour: 99
"""
        c = self._make_config_with_yaml(yaml_content)
        assert c.sites[0].rate_limit is not None
        assert c.sites[0].rate_limit.requests_per_hour == 99

    def test_global_rate_limit_override(self):
        yaml_content = """
global:
  rate_limit:
    requests_per_minute: 5
    requests_per_hour: 50
    requests_per_day: 300
    min_interval_seconds: 10
    max_interval_seconds: 60
sites: []
"""
        c = self._make_config_with_yaml(yaml_content)
        assert c.global_rate_limit.requests_per_minute == 5
        assert c.global_rate_limit.requests_per_hour == 50

    def test_captcha_indicators_loaded(self):
        yaml_content = """
sites: []
captcha_indicators:
  - selector: "#challenge"
    name: "Test Challenge"
  - selector: ".captcha"
    name: "Test Captcha"
"""
        c = self._make_config_with_yaml(yaml_content)
        assert len(c.captcha_indicators) == 2
        assert c.captcha_indicators[0].name == "Test Challenge"

    def test_get_site(self):
        yaml_content = """
sites:
  - name: mysite
    enabled: true
    url: "https://mysite.example.com"
"""
        c = self._make_config_with_yaml(yaml_content)
        site = c.get_site("mysite")
        assert site is not None
        assert site.name == "mysite"

        missing = c.get_site("nonexistent")
        assert missing is None

    def test_get_enabled_sites(self):
        yaml_content = """
sites:
  - name: s1
    enabled: true
    url: "https://s1.example.com"
  - name: s2
    enabled: true
    url: "https://s2.example.com"
"""
        c = self._make_config_with_yaml(yaml_content)
        enabled = c.get_enabled_sites()
        assert len(enabled) == 2

    def test_empty_yaml(self):
        c = self._make_config_with_yaml("")
        assert c.sites == []
        assert c.captcha_indicators == []
