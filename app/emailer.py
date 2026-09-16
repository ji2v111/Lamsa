import smtplib
from email.mime.text import MIMEText

from .config import SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_NAME, BASE_URL


def is_email_configured() -> bool:
    return bool(SMTP_USERNAME and SMTP_PASSWORD)


def send_welcome_email(to_email: str, tenant_name: str) -> bool:
    """يُرسل تلقائيًا عند إنشاء محل جديد — هذا هو إشعار "التسجيل" الفعلي لصاحب المحل."""
    login_url = f"{BASE_URL}/portal/login"
    body = (
        f"مرحبًا،\n\n"
        f"تم تجهيز لوحة \"{tenant_name}\" الخاصة بك على منصة لمسة.\n\n"
        f"للدخول إلى لوحتك ومتابعة تقييمات وملاحظات عملائك:\n"
        f"١) افتح الرابط: {login_url}\n"
        f"٢) اكتب بريدك هذا: {to_email}\n"
        f"٣) بيوصلك رمز دخول من 6 أرقام على نفس البريد — أدخله وخلاص.\n\n"
        f"ما فيه كلمة مرور تحفظها — بريدك هو مفتاح الدخول في كل مرة.\n"
    )
    if not is_email_configured():
        print(f"[DEV] لا يوجد إعداد SMTP — رسالة الترحيب لـ {to_email}:\n{body}")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"لوحتك على لمسة جاهزة — {tenant_name}"
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USERNAME}>"
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"[ERROR] فشل إرسال رسالة الترحيب لـ {to_email}: {e}")
        return False


def send_otp_email(to_email: str, code: str) -> bool:
    """يرسل رمز الدخول بالبريد. يرجع True لو نجح الإرسال الفعلي."""
    if not is_email_configured():
        # وضع التطوير المحلي بدون إعداد SMTP بعد: نطبع الرمز بسجل السيرفر
        # عشان تقدر تجرب تسجيل الدخول قبل ما تربط بريد حقيقي.
        print(f"[DEV] لا يوجد إعداد SMTP — رمز الدخول لـ {to_email} هو: {code}")
        return False

    msg = MIMEText(
        f"رمز الدخول للوحة محلك: {code}\n\nصالح لمدة 10 دقائق فقط. لو ما طلبت هذا الرمز، تجاهل الرسالة.",
        "plain",
        "utf-8",
    )
    msg["Subject"] = f"رمز الدخول: {code}"
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USERNAME}>"
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"[ERROR] فشل إرسال البريد لـ {to_email}: {e}")
        return False
