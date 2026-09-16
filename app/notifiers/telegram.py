import httpx
from .base import Notifier
from ..config import TELEGRAM_BOT_TOKEN


class TelegramNotifier(Notifier):
    name = "telegram_alerts"

    def is_configured(self, tenant) -> bool:
        return bool(TELEGRAM_BOT_TOKEN) and bool(getattr(tenant, "telegram_chat_id", None))

    def notify(self, tenant, feedback) -> bool:
        if not self.is_configured(tenant):
            return False

        severity_line = f"{feedback.severity} من 5" if feedback.severity else "غير محددة"
        text = (
            f"ملاحظة جديدة \u2014 {tenant.name}\n"
            f"الأولوية: {severity_line}\n"
            f"\u2014\u2014\u2014\n"
            f"{feedback.message}"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            r = httpx.post(url, json={"chat_id": tenant.telegram_chat_id, "text": text}, timeout=6)
            return r.status_code == 200
        except httpx.HTTPError:
            return False
