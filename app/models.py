import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean, LargeBinary
from sqlalchemy.orm import relationship
from .database import Base


class Tenant(Base):
    """محل واحد (مقهى / صالون / عيادة) — كل واحد له رابط ثابت خاص به."""
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    slug = Column(String(60), unique=True, index=True, nullable=False)  # الرابط العام (QR / NFC)
    owner_email = Column(String(180), nullable=True, index=True)  # بريد صاحب المحل — يدخل به لوحته
    otp_code = Column(String(10), nullable=True)
    otp_expires_at = Column(DateTime, nullable=True)
    google_review_link = Column(String(500), nullable=False)
    telegram_chat_id = Column(String(60), nullable=True)  # يُملأ لاحقًا من لوحة التحكم
    logo_image = Column(LargeBinary, nullable=True)
    logo_mime = Column(String(40), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    membership = relationship("Membership", back_populates="tenant", uselist=False, cascade="all, delete-orphan")
    scans = relationship("ScanEvent", back_populates="tenant", cascade="all, delete-orphan")
    feedbacks = relationship("Feedback", back_populates="tenant", cascade="all, delete-orphan")


class Membership(Base):
    """
    دورة حياة الاشتراك مبنية على تاريخين + مفتاح إيقاف يدوي، بدل حقل status ثابت:

    - manually_disabled: مفتاح إيقاف فوري يتجاوز كل شيء (الأولوية القصوى دائمًا)
    - end_date: تاريخ نهاية الفترة المدفوعة. لو فاضي = بدون تاريخ انتهاء (تحكم يدوي بحت)
    - grace_period_days: كم يوم سماح بعد end_date قبل الإيقاف الفعلي

    effective_status() يحسب الحالة الحقيقية لحظيًا من التاريخ الحالي — بدون أي جدولة
    أو عملية خلفية، فهو "أوتوماتيكي" فعليًا بمجرد ما تحدد start/end مرة وحدة.

    features يبقى المفتاح المرن لأي خاصية مستقبلية بدون تعديل الجدول.
    """
    __tablename__ = "memberships"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), unique=True, nullable=False)
    plan_name = Column(String(40), default="basic")
    features = Column(JSON, default=dict)

    manually_disabled = Column(Boolean, default=False)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    grace_period_days = Column(Integer, default=3)

    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    tenant = relationship("Tenant", back_populates="membership")

    def effective_status(self) -> str:
        if self.manually_disabled:
            return "inactive"
        if self.end_date is None:
            return "active"
        now = datetime.datetime.utcnow()
        if now <= self.end_date:
            return "active"
        grace_end = self.end_date + datetime.timedelta(days=self.grace_period_days or 0)
        if now <= grace_end:
            return "grace"
        return "inactive"

    def is_active(self) -> bool:
        return self.effective_status() in ("active", "grace")

    def days_remaining(self):
        """للعرض فقط بلوحة التحكم — كم يوم متبقي بالحالة الحالية."""
        if self.end_date is None:
            return None
        now = datetime.datetime.utcnow()
        status = self.effective_status()
        if status == "active":
            return max((self.end_date - now).days, 0)
        if status == "grace":
            grace_end = self.end_date + datetime.timedelta(days=self.grace_period_days or 0)
            return max((grace_end - now).days, 0)
        return 0

    def has_feature(self, key: str, default=False):
        return (self.features or {}).get(key, default)


class ScanEvent(Base):
    """كل مرة يلمس/يمسح فيها زبون البطاقة — رقم بسيط للتحليلات، لا يتحكم بأي عرض."""
    __tablename__ = "scan_events"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    path = Column(String(20), nullable=False)  # google | feedback | viewed
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    tenant = relationship("Tenant", back_populates="scans")


class Feedback(Base):
    """رسالة وصلت من زبون. severity يفيد المدير كسياق أولوية فعلي، مو شكلي."""
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    severity = Column(Integer, nullable=True)  # 1-5, اختياري
    message = Column(Text, nullable=False)
    customer_contact = Column(String(120), nullable=True)  # رقم/وسيلة تواصل اختيارية من الزبون
    manager_note = Column(Text, nullable=True)  # ملاحظة داخلية لصاحب المحل
    status = Column(String(20), default="new")  # new | seen | resolved
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    tenant = relationship("Tenant", back_populates="feedbacks")
