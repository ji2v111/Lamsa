import io
import string
import secrets
import datetime
import os

import qrcode
from fastapi import FastAPI, Request, Depends, Form, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, StreamingResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func

from .database import Base, engine, get_db
from .models import Tenant, Membership, ScanEvent, Feedback
from .notifiers.registry import send_alert
from .emailer import send_otp_email, send_welcome_email
from .config import BASE_URL, ADMIN_PASSWORD, SESSION_SECRET
from email_validator import validate_email, EmailNotValidError

Base.metadata.create_all(bind=engine)

app = FastAPI(title="لمسة")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
def to_ksa_format(dt, fmt="%Y-%m-%d %H:%M"):
    if not dt:
        return ""
    # إضافة فارق 3 ساعات لتحويل توقيت UTC المحفوظ إلى توقيت السعودية
    ksa_time = dt + datetime.timedelta(hours=3)
    return ksa_time.strftime(fmt)

templates.env.filters["ksa_time"] = to_ksa_format

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


DURATION_OPTIONS = {
    "1d": datetime.timedelta(days=1),
    "7d": datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
    "90d": datetime.timedelta(days=90),
    "365d": datetime.timedelta(days=365),
}

DURATION_LABELS = {
    "1d": "يوم واحد (تجربة)",
    "7d": "أسبوع",
    "30d": "شهر",
    "90d": "٣ أشهر",
    "365d": "سنة",
    "none": "بدون تاريخ انتهاء (تحكم يدوي فقط)",
}


def normalize_phone_for_wa(raw: str):
    """يحول رقم مكتوب بأي شكل (05..، +9665..، 5..) لصيغة يقبلها رابط wa.me."""
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "966" + digits[1:]
    elif len(digits) <= 10 and not digits.startswith("966"):
        digits = "966" + digits.lstrip("0")
    return digits


DEFAULT_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<path d="M50,16 L58.52,38.27 L82.34,39.49 L63.79,54.48 L69.99,77.51 L50,64.5 L30.01,77.51 '
    'L36.21,54.48 L17.66,39.49 L41.48,38.27 Z" fill="#211A16"/>'
    '<path d="M60,12 A8,8 0 0,1 75,15" fill="none" stroke="#B8863C" stroke-width="2.8" stroke-linecap="round"/>'
    '<path d="M54,3 A17,17 0 0,1 82,10" fill="none" stroke="#B8863C" stroke-width="2.8" stroke-linecap="round"/>'
    "</svg>"
)

MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2MB
ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/webp"}


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


# ---------- أدوات مساعدة ----------

def generate_slug(db: Session) -> str:
    alphabet = string.ascii_lowercase + string.digits
    while True:
        slug = "".join(secrets.choice(alphabet) for _ in range(7))
        if not db.query(Tenant).filter(Tenant.slug == slug).first():
            return slug


def is_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def get_tenant_or_none(db: Session, slug: str):
    return db.query(Tenant).filter(Tenant.slug == slug).first()


# ---------- الصفحة الرئيسية ----------

@app.get("/")
def root():
    return RedirectResponse("/admin")


# ---------- بوابة التوجيه العامة (هذي اللي تفتحها بطاقة NFC / QR) ----------

@app.get("/r/{slug}")
def customer_landing(slug: str, request: Request, db: Session = Depends(get_db)):
    tenant = get_tenant_or_none(db, slug)
    if not tenant:
        return PlainTextResponse("هذا الرابط غير صحيح.", status_code=404)

    if not tenant.membership or not tenant.membership.is_active():
        return templates.TemplateResponse("inactive.html", {"request": request, "tenant": tenant})

    db.add(ScanEvent(tenant_id=tenant.id, path="viewed"))
    db.commit()
    return templates.TemplateResponse("customer.html", {"request": request, "tenant": tenant})


@app.get("/r/{slug}/go")
def go_to_google(slug: str, db: Session = Depends(get_db)):
    tenant = get_tenant_or_none(db, slug)
    if not tenant or not tenant.membership or not tenant.membership.is_active():
        return RedirectResponse(f"/r/{slug}")

    db.add(ScanEvent(tenant_id=tenant.id, path="google"))
    db.commit()
    return RedirectResponse(tenant.google_review_link, status_code=302)


@app.get("/r/{slug}/feedback")
def feedback_form(slug: str, request: Request, db: Session = Depends(get_db)):
    tenant = get_tenant_or_none(db, slug)
    if not tenant or not tenant.membership or not tenant.membership.is_active():
        return RedirectResponse(f"/r/{slug}")

    return templates.TemplateResponse("feedback.html", {"request": request, "tenant": tenant})


@app.post("/r/{slug}/feedback")
def feedback_submit(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    message: str = Form(...),
    severity: str = Form(""),
    customer_contact: str = Form(""),
):
    tenant = get_tenant_or_none(db, slug)
    if not tenant or not tenant.membership or not tenant.membership.is_active():
        return RedirectResponse(f"/r/{slug}")

    message = message.strip()
    if not message:
        return RedirectResponse(f"/r/{slug}/feedback")

    severity_val = int(severity) if severity.isdigit() and 1 <= int(severity) <= 5 else None

    fb = Feedback(
        tenant_id=tenant.id,
        severity=severity_val,
        message=message,
        customer_contact=customer_contact.strip() or None,
    )
    db.add(fb)
    db.add(ScanEvent(tenant_id=tenant.id, path="feedback"))
    db.commit()
    db.refresh(fb)

    send_alert(tenant, fb)

    return templates.TemplateResponse("thanks.html", {"request": request, "tenant": tenant})


# ---------- تسجيل دخول لوحة التحكم ----------

@app.get("/admin/login")
def admin_login_page(request: Request):
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None, "show_logout": False})


@app.post("/admin/login")
def admin_login_submit(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["is_admin"] = True
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "error": "كلمة المرور غير صحيحة", "show_logout": False},
        status_code=401,
    )


@app.get("/admin/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login")


# ---------- لوحة التحكم ----------

@app.get("/admin")
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    for t in tenants:
        t.scan_count = db.query(ScanEvent).filter(ScanEvent.tenant_id == t.id).count()
        t.new_feedback_count = (
            db.query(Feedback).filter(Feedback.tenant_id == t.id, Feedback.status == "new").count()
        )

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "tenants": tenants,
            "welcomed": request.query_params.get("welcomed"),
            "welcomed_dev": request.query_params.get("welcomed_dev"),
            "show_logout": True,
        },
    )


@app.post("/admin/tenants/new")
def create_tenant(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    owner_email: str = Form(...),
    google_review_link: str = Form(...),
):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    try:
        valid = validate_email(owner_email.strip(), check_deliverability=False)
        clean_email = valid.normalized.lower()
    except EmailNotValidError:
        tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
        for t in tenants:
            t.scan_count = db.query(ScanEvent).filter(ScanEvent.tenant_id == t.id).count()
            t.new_feedback_count = (
                db.query(Feedback).filter(Feedback.tenant_id == t.id, Feedback.status == "new").count()
            )
        return templates.TemplateResponse(
            "admin_dashboard.html",
            {
                "request": request,
                "tenants": tenants,
                "create_error": "بريد صاحب المحل غير صحيح — تأكد من صيغته.",
                "show_logout": True,
            },
            status_code=400,
        )

    tenant = Tenant(
        name=name.strip(),
        slug=generate_slug(db),
        owner_email=clean_email,
        google_review_link=google_review_link.strip(),
    )
    db.add(tenant)
    db.flush()

    membership = Membership(
        tenant_id=tenant.id,
        plan_name="basic",
        features={"telegram_alerts": False, "max_cards": 1},
    )
    db.add(membership)
    db.commit()

    welcome_sent = send_welcome_email(clean_email, tenant.name)
    flag = "welcomed" if welcome_sent else "welcomed_dev"
    return RedirectResponse(f"/admin?{flag}=1", status_code=302)


@app.get("/admin/tenants/{tenant_id}")
def tenant_detail(tenant_id: int, request: Request, db: Session = Depends(get_db)):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return PlainTextResponse("غير موجود", status_code=404)

    feedbacks = (
        db.query(Feedback).filter(Feedback.tenant_id == tenant.id).order_by(Feedback.created_at.desc()).all()
    )

    since = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    google_scans = (
        db.query(ScanEvent)
        .filter(ScanEvent.tenant_id == tenant.id, ScanEvent.path == "google", ScanEvent.created_at >= since)
        .count()
    )
    feedback_scans = (
        db.query(ScanEvent)
        .filter(ScanEvent.tenant_id == tenant.id, ScanEvent.path == "feedback", ScanEvent.created_at >= since)
        .count()
    )

    return templates.TemplateResponse(
        "admin_tenant.html",
        {
            "request": request,
            "tenant": tenant,
            "feedbacks": feedbacks,
            "google_scans": google_scans,
            "feedback_scans": feedback_scans,
            "public_url": f"{BASE_URL}/r/{tenant.slug}",
            "duration_labels": DURATION_LABELS,
            "show_logout": True,
        },
    )


@app.post("/admin/tenants/{tenant_id}/renew")
def renew_tenant(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    duration: str = Form(...),
    grace_days: str = Form("3"),
):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant and tenant.membership:
        now = datetime.datetime.utcnow()
        tenant.membership.start_date = now
        tenant.membership.end_date = now + DURATION_OPTIONS[duration] if duration in DURATION_OPTIONS else None
        tenant.membership.grace_period_days = int(grace_days) if grace_days.isdigit() else 3
        tenant.membership.manually_disabled = False  # التجديد يلغي أي إيقاف يدوي سابق
        db.commit()

    return RedirectResponse(f"/admin/tenants/{tenant_id}", status_code=302)


@app.post("/admin/tenants/{tenant_id}/toggle-manual")
def toggle_manual(tenant_id: int, request: Request, db: Session = Depends(get_db)):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant and tenant.membership:
        tenant.membership.manually_disabled = not tenant.membership.manually_disabled
        db.commit()

    return RedirectResponse(f"/admin/tenants/{tenant_id}", status_code=302)


@app.post("/admin/tenants/{tenant_id}/delete")
def delete_tenant(tenant_id: int, request: Request, db: Session = Depends(get_db)):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant:
        db.delete(tenant)  # cascade تحذف Membership وScanEvent وFeedback تلقائيًا
        db.commit()

    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/tenants/{tenant_id}/notify-settings")
def update_notify_settings(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    telegram_chat_id: str = Form(""),
    telegram_alerts: str = Form(None),
):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant and tenant.membership:
        tenant.telegram_chat_id = telegram_chat_id.strip() or None
        features = dict(tenant.membership.features or {})
        features["telegram_alerts"] = bool(telegram_alerts)
        tenant.membership.features = features
        db.commit()

    return RedirectResponse(f"/admin/tenants/{tenant_id}", status_code=302)


@app.post("/admin/tenants/{tenant_id}/owner-email")
def update_owner_email(tenant_id: int, request: Request, db: Session = Depends(get_db), owner_email: str = Form(...)):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant:
        try:
            valid = validate_email(owner_email.strip(), check_deliverability=False)
            tenant.owner_email = valid.normalized.lower()
            db.commit()
        except EmailNotValidError:
            pass  # نتجاهل بصمت هنا؛ لوحة الأدمن للمالك نفسه، والتحقق الأساسي يصير عند إنشاء المحل

    return RedirectResponse(f"/admin/tenants/{tenant_id}", status_code=302)


@app.get("/admin/feedback/{feedback_id}")
def feedback_detail(feedback_id: int, request: Request, db: Session = Depends(get_db)):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not fb:
        return PlainTextResponse("غير موجود", status_code=404)

    wa_number = normalize_phone_for_wa(fb.customer_contact) if fb.customer_contact else None

    return templates.TemplateResponse(
        "feedback_detail.html",
        {
            "request": request,
            "fb": fb,
            "tenant": fb.tenant,
            "wa_number": wa_number,
            "back_url": f"/admin/tenants/{fb.tenant_id}",
            "back_label": f"‹ {fb.tenant.name}",
            "update_action": f"/admin/feedback/{fb.id}/update",
            "show_logout": True,
        },
    )


@app.post("/admin/feedback/{feedback_id}/update")
def update_feedback(
    feedback_id: int,
    request: Request,
    db: Session = Depends(get_db),
    status: str = Form("new"),
    manager_note: str = Form(""),
):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if fb:
        fb.status = status if status in ("new", "seen", "resolved") else fb.status
        fb.manager_note = manager_note.strip() or None
        db.commit()
        return RedirectResponse(f"/admin/feedback/{feedback_id}", status_code=302)

    return RedirectResponse("/admin", status_code=302)


@app.get("/admin/tenants/{tenant_id}/qr.png")
def tenant_qr(tenant_id: int, request: Request, db: Session = Depends(get_db)):
    if not is_admin(request):
        return RedirectResponse("/admin/login")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return PlainTextResponse("غير موجود", status_code=404)

    url = f"{BASE_URL}/r/{tenant.slug}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.get("/tenant/{slug}/logo.png")
def tenant_logo(slug: str, db: Session = Depends(get_db)):
    tenant = get_tenant_or_none(db, slug)
    if tenant and tenant.logo_image:
        return Response(content=tenant.logo_image, media_type=tenant.logo_mime or "image/png")
    return Response(content=DEFAULT_LOGO_SVG, media_type="image/svg+xml")


# ---------- بوابة صاحب المحل — تسجيل دخول برمز يُرسل للبريد، بدون كلمة مرور ثابتة ----------

OTP_TTL_MINUTES = 10


def portal_tenant_or_none(request: Request, db: Session):
    tid = request.session.get("portal_tenant_id")
    if not tid:
        return None
    return db.query(Tenant).filter(Tenant.id == tid).first()


@app.get("/portal/login")
def portal_login_page(request: Request):
    return templates.TemplateResponse(
        "portal_login.html", {"request": request, "error": None, "sent": False, "show_logout": False}
    )


@app.post("/portal/login")
def portal_login_submit(request: Request, db: Session = Depends(get_db), email: str = Form(...)):
    raw_email = email.strip()

    try:
        valid = validate_email(raw_email, check_deliverability=False)
    except EmailNotValidError:
        return templates.TemplateResponse(
            "portal_login.html",
            {"request": request, "error": "صيغة البريد غير صحيحة — تأكد وأعد المحاولة.", "sent": False, "show_logout": False},
            status_code=400,
        )

    clean_email = valid.normalized.lower()
    tenant = db.query(Tenant).filter(Tenant.owner_email == clean_email).first()

    if tenant:
        code = generate_otp_code()
        tenant.otp_code = code
        tenant.otp_expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=OTP_TTL_MINUTES)
        db.commit()
        send_otp_email(clean_email, code)

    request.session["pending_login_email"] = clean_email
    # نفس الرسالة تظهر سواء كان البريد مسجّل أو لا، حتى لا نكشف أي بريد مسجّل بالنظام لطرف غير معروف
    return templates.TemplateResponse(
        "portal_verify.html", {"request": request, "email": clean_email, "error": None, "show_logout": False}
    )


@app.post("/portal/verify")
def portal_verify_submit(request: Request, db: Session = Depends(get_db), code: str = Form(...)):
    email = request.session.get("pending_login_email")
    code = code.strip()

    def fail(msg):
        return templates.TemplateResponse(
            "portal_verify.html", {"request": request, "email": email, "error": msg, "show_logout": False}, status_code=400
        )

    if not email:
        return RedirectResponse("/portal/login")
    if not (code.isdigit() and len(code) == 6):
        return fail("الرمز لازم يكون 6 أرقام.")

    tenant = db.query(Tenant).filter(Tenant.owner_email == email).first()
    if (
        not tenant
        or not tenant.otp_code
        or tenant.otp_code != code
        or not tenant.otp_expires_at
        or datetime.datetime.utcnow() > tenant.otp_expires_at
    ):
        return fail("الرمز غير صحيح أو انتهت صلاحيته — اطلب رمز جديد.")

    # الرمز يُستخدم مرة واحدة فقط
    tenant.otp_code = None
    tenant.otp_expires_at = None
    db.commit()

    request.session.pop("pending_login_email", None)
    request.session["portal_tenant_id"] = tenant.id
    return RedirectResponse("/portal", status_code=302)


@app.get("/portal/logout")
def portal_logout(request: Request):
    request.session.pop("portal_tenant_id", None)
    return RedirectResponse("/portal/login")


@app.get("/portal")
def portal_dashboard(request: Request, db: Session = Depends(get_db)):
    tenant = portal_tenant_or_none(request, db)
    if not tenant:
        return RedirectResponse("/portal/login")

    feedbacks = (
        db.query(Feedback).filter(Feedback.tenant_id == tenant.id).order_by(Feedback.created_at.desc()).all()
    )

    return templates.TemplateResponse(
        "portal_dashboard.html",
        {
            "request": request,
            "tenant": tenant,
            "feedbacks": feedbacks,
            "logo_error": request.query_params.get("logo_error"),
            "show_logout": True,
            "logout_url": "/portal/logout",
        },
    )


@app.get("/portal/feedback/{feedback_id}")
def portal_feedback_detail(feedback_id: int, request: Request, db: Session = Depends(get_db)):
    tenant = portal_tenant_or_none(request, db)
    if not tenant:
        return RedirectResponse("/portal/login")

    fb = db.query(Feedback).filter(Feedback.id == feedback_id, Feedback.tenant_id == tenant.id).first()
    if not fb:
        return PlainTextResponse("هذه الملاحظة غير موجودة بمحلك.", status_code=404)

    wa_number = normalize_phone_for_wa(fb.customer_contact) if fb.customer_contact else None

    return templates.TemplateResponse(
        "feedback_detail.html",
        {
            "request": request,
            "fb": fb,
            "tenant": tenant,
            "wa_number": wa_number,
            "back_url": "/portal",
            "back_label": "‹ لوحتي",
            "update_action": f"/portal/feedback/{fb.id}/update",
            "show_logout": True,
            "logout_url": "/portal/logout",
        },
    )


@app.post("/portal/feedback/{feedback_id}/update")
def portal_update_feedback(
    feedback_id: int,
    request: Request,
    db: Session = Depends(get_db),
    status: str = Form("new"),
    manager_note: str = Form(""),
):
    tenant = portal_tenant_or_none(request, db)
    if not tenant:
        return RedirectResponse("/portal/login")

    fb = db.query(Feedback).filter(Feedback.id == feedback_id, Feedback.tenant_id == tenant.id).first()
    if fb:
        fb.status = status if status in ("new", "seen", "resolved") else fb.status
        fb.manager_note = manager_note.strip() or None
        db.commit()

    return RedirectResponse(f"/portal/feedback/{feedback_id}", status_code=302)


@app.post("/portal/logo")
def upload_logo(request: Request, db: Session = Depends(get_db), logo: UploadFile = File(...)):
    tenant = portal_tenant_or_none(request, db)
    if not tenant:
        return RedirectResponse("/portal/login")

    contents = logo.file.read()
    if logo.content_type in ALLOWED_LOGO_TYPES and 0 < len(contents) <= MAX_LOGO_BYTES:
        tenant.logo_image = contents
        tenant.logo_mime = logo.content_type
        db.commit()
        return RedirectResponse("/portal", status_code=302)

    return RedirectResponse("/portal?logo_error=1", status_code=302)
