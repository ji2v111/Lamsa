import os
import resend

resend.api_key = os.getenv("RESEND_API_KEY", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev").strip()

def send_otp_email(to_email: str, code: str) -> bool:
    if not resend.api_key:
        print(f"[DEV] Missing RESEND_API_KEY. OTP for {to_email}: {code}")
        return True

    params = {
        "from": EMAIL_FROM,
        "to": to_email,
        "subject": "رمز التحقق - لمسة",
        "html": f"""
        <div dir="rtl" style="font-family: Arial, sans-serif; padding: 24px; background-color: #f8fafc; text-align: center;">
            <div style="max-width: 440px; margin: 0 auto; background: #ffffff; padding: 32px; border-radius: 12px; border: 1px solid #e2e8f0;">
                <h2 style="color: #0f172a; margin-bottom: 12px;">تسجيل الدخول</h2>
                <p style="color: #475569; font-size: 15px; margin-bottom: 24px;">رمز التحقق الخاص بك هو:</p>
                <div style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #2563eb; background: #eff6ff; padding: 14px; border-radius: 8px; display: inline-block;">
                    {code}
                </div>
                <p style="color: #94a3b8; font-size: 13px; margin-top: 24px;">صلاحية هذا الرمز مؤقتة. إذا لم تكن أنت من طلبه، تجاهل هذه الرسالة.</p>
            </div>
        </div>
        """
    }

    try:
        response = resend.Emails.send(params)
        print(f"[INFO] Email sent successfully to {to_email}. Response: {response}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to send email via Resend: {e}")
        return False