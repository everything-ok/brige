from typing import Optional

from .detector import CaptchaDetector
from .notifier import HumanNotifier


class SecurityManager:
    """安全管理器"""

    def __init__(self, config):
        self.config = config
        self.detector = CaptchaDetector(config)
        self.notifier = HumanNotifier(config)

    async def check_and_handle(self, page, site_name: str,
                              wait_minutes: Optional[int] = None) -> bool:
        """检查验证并处理

        wait_minutes: 覆盖配置的等待时长。IDE/API 调用应传较小的值
        （请求挂在等验证码上会让 IDE 侧长时间无响应），
        交互式 CLI 场景则用配置里的 max_wait_minutes。
        """
        has_captcha, captcha_type = await self.detector.check(page)

        if not has_captcha:
            return True

        await self.notifier.notify_captcha(page, captcha_type, site_name)

        if self.config.security.auto_pause:
            resolved = await self.detector.wait_for_resolution(
                page,
                timeout_minutes=(wait_minutes if wait_minutes is not None
                                 else self.config.security.max_wait_minutes),
            )
            return resolved

        return False
