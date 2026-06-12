import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel, Field
from typing import Optional

SQLALCHEMY_DATABASE_URL = "sqlite:///./meeting_actions.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class NotificationConfig(BaseModel):
    enabled: bool = Field(default_factory=lambda: os.getenv("FEISHU_NOTIFY_ENABLED", "false").lower() == "true")
    webhook_url: Optional[str] = Field(default_factory=lambda: os.getenv("FEISHU_WEBHOOK_URL", ""))
    reminder_log_path: str = Field(default_factory=lambda: os.getenv("REMINDER_LOG_PATH", "./reminder.log"))
    scan_hour: int = Field(default_factory=lambda: int(os.getenv("REMINDER_SCAN_HOUR", "9")))
    scan_minute: int = Field(default_factory=lambda: int(os.getenv("REMINDER_SCAN_MINUTE", "0")))

    def is_webhook_available(self) -> bool:
        return self.enabled and bool(self.webhook_url)


notification_config = NotificationConfig()
