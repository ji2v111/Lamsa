from .telegram import TelegramNotifier

# كل قناة مربوطة بمفتاح خاصية داخل membership.features.
# لإضافة واتساب لاحقًا: أنشئ WhatsAppNotifier بنفس شكل TelegramNotifier،
# ثم أضف سطر واحد هنا: "whatsapp_alerts": WhatsAppNotifier().
# لا حاجة لتعديل main.py ولا أي شيء آخر.
AVAILABLE_NOTIFIERS = {
    "telegram_alerts": TelegramNotifier(),
}


def send_alert(tenant, feedback) -> bool:
    """يمر على كل القنوات المفعّلة لهذا المحل ويحاول الإرسال بكل وحدة منها."""
    if not tenant.membership:
        return False
    features = tenant.membership.features or {}
    sent_any = False
    for feature_key, notifier in AVAILABLE_NOTIFIERS.items():
        if features.get(feature_key) and notifier.notify(tenant, feedback):
            sent_any = True
    return sent_any
