"""DBモデルとエンジン設定。

Railway の PostgreSQL（DATABASE_URL）を使う。未設定時はローカルの SQLite で動く。
"""
import os
from datetime import datetime

from sqlalchemy import create_engine, String, Text, DateTime, JSON, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./archive.db")
# Railway は postgres:// で渡してくるので SQLAlchemy 用に補正
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    meeting_date: Mapped[str] = mapped_column(String(32), default="")
    company_name: Mapped[str] = mapped_column(String(255), default="")
    meeting_type: Mapped[str] = mapped_column(String(64), default="")
    temperature: Mapped[str] = mapped_column(String(8), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    source_file: Mapped[str] = mapped_column(String(255), default="")
    raw_transcript: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


def init_db() -> None:
    Base.metadata.create_all(engine)
