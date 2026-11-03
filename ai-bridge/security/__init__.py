from .detector import CaptchaDetector
from .notifier import HumanNotifier


class SecurityManager:
    """安全管理器"""

    def __init__(self, config):
        self.config = config
        self.detector = CaptchaDetector(config)
        self.notifier = HumanNotifier(config)

    async def check_and_handle(self, page, site_name: str) -> bool:
        """检查验证并处理"""
        has_captcha, captcha_type = await self.detector.check(page)

        if not has_captcha:
            return True

        await self.notifier.notify_captcha(page, captcha_type, site_name)

        if self.config.security.auto_pause:
            resolved = await self.detector.wait_for_resolution(
                page,
                timeout_minutes=self.config.security.max_wait_minutes,
            )
            return resolved

        return False
