import asyncio
import platform
import subprocess
from pathlib import Path
from datetime import datetime
from rich.console import Console

console = Console()


class HumanNotifier:
    """人工通知器"""

    def __init__(self, config):
        self.config = config
        self.screenshots_dir = Path("logs/screenshots")
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    async def notify_captcha(self, page, captcha_type: str, site_name: str):
        """通知需要人工处理验证"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        message = f"🔒 {site_name} 需要人工验证: {captcha_type}"

        console.print(f"\n[bold red]{'=' * 60}[/bold red]")
        console.print(f"[bold yellow]{message}[/bold yellow]")
        console.print(f"[bold red]{'=' * 60}[/bold red]\n")

        if self.config.security.screenshot_on_captcha:
            try:
                screenshot_path = self.screenshots_dir / f"captcha_{timestamp}.png"
                await page.screenshot(path=str(screenshot_path))
                console.print(f"[cyan]📸 截图已保存: {screenshot_path}[/cyan]")
            except Exception as e:
                console.print(f"[yellow]截图失败: {e}[/yellow]")

        if self.config.security.notification_sound:
            self._play_sound()

        if self.config.security.desktop_notification:
            self._desktop_notify(message, site_name)

    def _play_sound(self):
        """播放提示音"""
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.run(
                    ["afplay", "/System/Library/Sounds/Ping.aiff"],
                    capture_output=True,
                )
            elif system == "Linux":
                subprocess.run(
                    ["paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
                    capture_output=True,
                )
            elif system == "Windows":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            print("\a")

    def _desktop_notify(self, message: str, title: str):
        """发送桌面通知"""
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.run([
                    "osascript", "-e",
                    f'display notification "{message}" with title "{title}"'
                ], capture_output=True)
            elif system == "Linux":
                subprocess.run([
                    "notify-send", "-u", "critical", title, message
                ], capture_output=True)
            elif system == "Windows":
                try:
                    from win10toast import ToastNotifier
                    toaster = ToastNotifier()
                    toaster.show_toast(title, message, duration=10)
                except ImportError:
                    pass
        except Exception:
            pass
