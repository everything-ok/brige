import os
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

BASE_DIR     = Path(__file__).parent
PROFILES_DIR = BASE_DIR / "profiles"
LOGS_DIR     = BASE_DIR / "logs"

PROFILES_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


@dataclass
class RateLimitConfig:
    requests_per_minute: int = 2
    requests_per_hour: int = 30
    requests_per_day: int = 200
    min_interval_seconds: int = 25
    max_interval_seconds: int = 120


@dataclass
class BehaviorConfig:
    typing_speed_min_ms: int = 40
    typing_speed_max_ms: int = 180
    typing_mistake_rate: float = 0.02
    mouse_speed_min: int = 200
    mouse_speed_max: int = 800
    scroll_probability: float = 0.3
    pause_probability: float = 0.15
    pause_duration_min: float = 1.0
    pause_duration_max: float = 5.0


@dataclass
class CooldownConfig:
    initial_seconds: int = 300
    max_seconds: int = 3600
    multiplier: float = 2.0
    reset_after_success: int = 3


@dataclass
class SecurityConfig:
    auto_pause: bool = True
    notification_sound: bool = True
    desktop_notification: bool = True
    max_wait_minutes: int = 30
    screenshot_on_captcha: bool = True


@dataclass
class SiteConfig:
    name: str
    url: str
    enabled: bool = True
    priority: int = 10
    selectors: Dict[str, str] = field(default_factory=dict)
    models: Dict[str, str] = field(default_factory=dict)
    rate_limit: Optional[RateLimitConfig] = None


@dataclass
class CaptchaIndicator:
    selector: str
    name: str


class Config:
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 8080))
    API_KEY = os.getenv("API_KEY", "")  # 留空则不校验，本地使用无需配置
    HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"

    def __init__(self):
        self.global_rate_limit = RateLimitConfig()
        self.behavior = BehaviorConfig()
        self.cooldown = CooldownConfig()
        self.security = SecurityConfig()
        self.sites: List[SiteConfig] = []
        self.captcha_indicators: List[CaptchaIndicator] = []
        self._load_yaml()

    def _load_yaml(self):
        yaml_path = BASE_DIR / "sites.yaml"
        if not yaml_path.exists():
            return

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        g = data.get("global", {})

        if "rate_limit" in g:
            self.global_rate_limit = RateLimitConfig(**g["rate_limit"])
        if "behavior" in g:
            self.behavior = BehaviorConfig(**g["behavior"])
        if "cooldown" in g:
            self.cooldown = CooldownConfig(**g["cooldown"])
        if "security" in g:
            self.security = SecurityConfig(**g["security"])

        for site_data in data.get("sites", []):
            site_data = dict(site_data)
            rate_limit = None
            if "rate_limit" in site_data:
                rate_limit = RateLimitConfig(**site_data.pop("rate_limit"))

            site = SiteConfig(
                name=site_data.get("name", "unknown"),
                url=site_data.get("url", ""),
                enabled=site_data.get("enabled", True),
                priority=site_data.get("priority", 10),
                selectors=site_data.get("selectors", {}),
                models=site_data.get("models", {}),
                rate_limit=rate_limit,
            )
            if site.enabled and site.url:
                self.sites.append(site)

        self.sites.sort(key=lambda x: x.priority)

        for ci in data.get("captcha_indicators", []):
            self.captcha_indicators.append(
                CaptchaIndicator(selector=ci["selector"], name=ci["name"])
            )

    def get_site(self, name: str) -> Optional[SiteConfig]:
        for site in self.sites:
            if site.name == name:
                return site
        return None

    def get_enabled_sites(self) -> List[SiteConfig]:
        return [s for s in self.sites if s.enabled]


config = Config()
