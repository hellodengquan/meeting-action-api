from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Enum, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from config import Base


class ActionStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class Priority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    meeting_date = Column(DateTime, nullable=False)
    location = Column(String(255))
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    actions = relationship("ActionItem", back_populates="meeting", cascade="all, delete-orphan")


class Assignee(Base):
    __tablename__ = "assignees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255))
    department = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    actions = relationship("ActionItem", back_populates="assignee")


class ActionItem(Base):
    __tablename__ = "action_items"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    assignee_id = Column(Integer, ForeignKey("assignees.id"), nullable=False)
    status = Column(Enum(ActionStatus), default=ActionStatus.PENDING, nullable=False)
    priority = Column(Enum(Priority), default=Priority.MEDIUM, nullable=False)
    due_date = Column(DateTime, nullable=False)
    completed_at = Column(DateTime)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    meeting = relationship("Meeting", back_populates="actions")
    assignee = relationship("Assignee", back_populates="actions")


class ReminderSent(Base):
    __tablename__ = "reminder_sent"

    id = Column(Integer, primary_key=True, index=True)
    action_id = Column(Integer, ForeignKey("action_items.id", ondelete="CASCADE"), nullable=False)
    sent_date = Column(String(10), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    channel = Column(String(20), nullable=False, default="feishu")

    __table_args__ = (
        UniqueConstraint("action_id", "sent_date", name="uq_action_sent_date"),
    )
