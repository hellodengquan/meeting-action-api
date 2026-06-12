from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
from models import ActionStatus, Priority


class AssigneeBase(BaseModel):
    name: str = Field(..., max_length=100)
    email: Optional[str] = Field(None, max_length=255)
    department: Optional[str] = Field(None, max_length=100)


class AssigneeCreate(AssigneeBase):
    pass


class AssigneeUpdate(AssigneeBase):
    name: Optional[str] = Field(None, max_length=100)


class Assignee(AssigneeBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class MeetingBase(BaseModel):
    title: str = Field(..., max_length=255)
    meeting_date: datetime
    location: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None


class MeetingCreate(MeetingBase):
    pass


class MeetingUpdate(MeetingBase):
    title: Optional[str] = Field(None, max_length=255)
    meeting_date: Optional[datetime] = None


class Meeting(MeetingBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ActionItemBase(BaseModel):
    title: str = Field(..., max_length=500)
    description: Optional[str] = None
    assignee_id: int
    meeting_id: Optional[int] = None
    status: ActionStatus = ActionStatus.PENDING
    priority: Priority = Priority.MEDIUM
    due_date: datetime
    notes: Optional[str] = None


class ActionItemCreate(ActionItemBase):
    pass


class ActionItemUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    assignee_id: Optional[int] = None
    meeting_id: Optional[int] = None
    status: Optional[ActionStatus] = None
    priority: Optional[Priority] = None
    due_date: Optional[datetime] = None
    notes: Optional[str] = None


class ActionItem(ActionItemBase):
    id: int
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    assignee: Optional[Assignee] = None
    meeting: Optional[Meeting] = None

    class Config:
        from_attributes = True


class ActionItemListResponse(BaseModel):
    total: int
    items: List[ActionItem]


class ReminderResponse(BaseModel):
    overdue: List[ActionItem]
    due_today: List[ActionItem]
    due_this_week: List[ActionItem]


class DailySkipCount(BaseModel):
    date: str
    skipped_count: int


class IdempotencySummary(BaseModel):
    period_days: int
    total_skipped: int
    daily_skip: List[DailySkipCount]
    most_skipped_action_id: Optional[int]
    most_skipped_action_count: int
    data_start_date: str
    data_end_date: str
