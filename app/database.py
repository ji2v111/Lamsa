import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# لو ما فيه DATABASE_URL (يعني وضع تجربة محلي) نستخدم ملف SQLite بدل Postgres.
# نفس الكود يشتغل مع الاثنين لأن SQLAlchemy يتعامل مع الفروقات بينهم.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if DATABASE_URL:
    # Supabase تعطي رابط يبدأ بـ postgres:// وSQLAlchemy الحديث يحتاج postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    DATABASE_URL = "sqlite:///./local_dev.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
