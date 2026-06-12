from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from datetime import datetime, timedelta
from typing import Optional, List

from config import engine, get_db, Base, notification_config
import models
import schemas
import notifier
import scheduler

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start_scheduler()
    yield
    scheduler.stop_scheduler()


app = FastAPI(
    title="会议行动项管理 API",
    description="登记、跟踪会议纪要中的行动项，支持负责人管理、状态跟踪、到期提醒和多维度筛选",
    version="1.1.0",
    lifespan=lifespan,
)


@app.get("/")
def root():
    return {
        "message": "会议行动项管理 API",
        "docs": "/docs",
        "redoc": "/redoc",
    }


# ==================== 通知配置 ====================

@app.get("/config/notification", tags=["通知配置"])
def get_notification_config():
    return {
        "enabled": notification_config.enabled,
        "has_webhook_url": bool(notification_config.webhook_url),
        "webhook_url": (notification_config.webhook_url[:20] + "...") if notification_config.webhook_url else "",
        "reminder_log_path": notification_config.reminder_log_path,
        "scan_schedule": f"{notification_config.scan_hour:02d}:{notification_config.scan_minute:02d}",
    }


@app.post("/reminders/run-now", tags=["到期提醒"])
def run_reminders_now(db: Session = Depends(get_db)):
    notifier.cleanup_expired_sent_records(db)
    items = notifier.fetch_upcoming_items(db, within_days=7)
    result = notifier.send_reminders(items, db_session=db)
    return {"items_count": len(items), "result": result}


# ==================== 负责人接口 ====================

@app.post("/assignees/", response_model=schemas.Assignee, tags=["负责人管理"])
def create_assignee(assignee: schemas.AssigneeCreate, db: Session = Depends(get_db)):
    db_assignee = models.Assignee(**assignee.model_dump())
    db.add(db_assignee)
    db.commit()
    db.refresh(db_assignee)
    return db_assignee


@app.get("/assignees/", response_model=List[schemas.Assignee], tags=["负责人管理"])
def list_assignees(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(100, ge=1, le=500, description="返回条数"),
    department: Optional[str] = Query(None, description="按部门筛选"),
    db: Session = Depends(get_db),
):
    query = db.query(models.Assignee)
    if department:
        query = query.filter(models.Assignee.department == department)
    return query.offset(skip).limit(limit).all()


@app.get("/assignees/{assignee_id}", response_model=schemas.Assignee, tags=["负责人管理"])
def get_assignee(assignee_id: int, db: Session = Depends(get_db)):
    db_assignee = db.query(models.Assignee).filter(models.Assignee.id == assignee_id).first()
    if not db_assignee:
        raise HTTPException(status_code=404, detail="负责人不存在")
    return db_assignee


@app.put("/assignees/{assignee_id}", response_model=schemas.Assignee, tags=["负责人管理"])
def update_assignee(
    assignee_id: int,
    assignee_update: schemas.AssigneeUpdate,
    db: Session = Depends(get_db),
):
    db_assignee = db.query(models.Assignee).filter(models.Assignee.id == assignee_id).first()
    if not db_assignee:
        raise HTTPException(status_code=404, detail="负责人不存在")
    update_data = assignee_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_assignee, key, value)
    db.commit()
    db.refresh(db_assignee)
    return db_assignee


@app.delete("/assignees/{assignee_id}", tags=["负责人管理"])
def delete_assignee(assignee_id: int, db: Session = Depends(get_db)):
    db_assignee = db.query(models.Assignee).filter(models.Assignee.id == assignee_id).first()
    if not db_assignee:
        raise HTTPException(status_code=404, detail="负责人不存在")
    action_count = db.query(models.ActionItem).filter(models.ActionItem.assignee_id == assignee_id).count()
    if action_count > 0:
        raise HTTPException(status_code=400, detail=f"该负责人尚有 {action_count} 个行动项，无法删除")
    db.delete(db_assignee)
    db.commit()
    return {"message": "删除成功"}


# ==================== 会议接口 ====================

@app.post("/meetings/", response_model=schemas.Meeting, tags=["会议管理"])
def create_meeting(meeting: schemas.MeetingCreate, db: Session = Depends(get_db)):
    db_meeting = models.Meeting(**meeting.model_dump())
    db.add(db_meeting)
    db.commit()
    db.refresh(db_meeting)
    return db_meeting


@app.get("/meetings/", response_model=List[schemas.Meeting], tags=["会议管理"])
def list_meetings(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return db.query(models.Meeting).order_by(models.Meeting.meeting_date.desc()).offset(skip).limit(limit).all()


@app.get("/meetings/{meeting_id}", response_model=schemas.Meeting, tags=["会议管理"])
def get_meeting(meeting_id: int, db: Session = Depends(get_db)):
    db_meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not db_meeting:
        raise HTTPException(status_code=404, detail="会议不存在")
    return db_meeting


@app.delete("/meetings/{meeting_id}", tags=["会议管理"])
def delete_meeting(meeting_id: int, db: Session = Depends(get_db)):
    db_meeting = db.query(models.Meeting).filter(models.Meeting.id == meeting_id).first()
    if not db_meeting:
        raise HTTPException(status_code=404, detail="会议不存在")
    db.delete(db_meeting)
    db.commit()
    return {"message": "删除成功"}


# ==================== 行动项接口 ====================

@app.post("/actions/", response_model=schemas.ActionItem, tags=["行动项管理"])
def create_action(action: schemas.ActionItemCreate, db: Session = Depends(get_db)):
    db_assignee = db.query(models.Assignee).filter(models.Assignee.id == action.assignee_id).first()
    if not db_assignee:
        raise HTTPException(status_code=400, detail="指定的负责人不存在")
    if action.meeting_id:
        db_meeting = db.query(models.Meeting).filter(models.Meeting.id == action.meeting_id).first()
        if not db_meeting:
            raise HTTPException(status_code=400, detail="指定的会议不存在")
    db_action = models.ActionItem(**action.model_dump())
    db.add(db_action)
    db.commit()
    db.refresh(db_action)
    return db_action


@app.get("/actions/", response_model=schemas.ActionItemListResponse, tags=["行动项管理"])
def list_actions(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(50, ge=1, le=500, description="返回条数"),
    status: Optional[List[models.ActionStatus]] = Query(None, description="按状态筛选，可多选"),
    priority: Optional[List[models.Priority]] = Query(None, description="按优先级筛选，可多选"),
    assignee_id: Optional[int] = Query(None, description="按负责人ID筛选"),
    meeting_id: Optional[int] = Query(None, description="按会议ID筛选"),
    due_before: Optional[datetime] = Query(None, description="到期时间早于此时间"),
    due_after: Optional[datetime] = Query(None, description="到期时间晚于此时间"),
    overdue_only: Optional[bool] = Query(False, description="仅显示已逾期（未完成）"),
    keyword: Optional[str] = Query(None, description="关键词搜索（标题/描述）"),
    sort_by: str = Query("due_date", pattern="^(due_date|priority|status|created_at|updated_at)$", description="排序字段"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$", description="排序方向"),
    db: Session = Depends(get_db),
):
    query = db.query(models.ActionItem)

    if status:
        query = query.filter(models.ActionItem.status.in_(status))
    if priority:
        query = query.filter(models.ActionItem.priority.in_(priority))
    if assignee_id:
        query = query.filter(models.ActionItem.assignee_id == assignee_id)
    if meeting_id:
        query = query.filter(models.ActionItem.meeting_id == meeting_id)
    if due_before:
        query = query.filter(models.ActionItem.due_date <= due_before)
    if due_after:
        query = query.filter(models.ActionItem.due_date >= due_after)
    if overdue_only:
        now = datetime.utcnow()
        query = query.filter(
            and_(
                models.ActionItem.due_date < now,
                models.ActionItem.status.notin_([models.ActionStatus.COMPLETED, models.ActionStatus.CANCELLED]),
            )
        )
    if keyword:
        like_pattern = f"%{keyword}%"
        query = query.filter(or_(models.ActionItem.title.like(like_pattern), models.ActionItem.description.like(like_pattern)))

    total = query.count()

    sort_column = getattr(models.ActionItem, sort_by)
    if sort_order == "desc":
        sort_column = sort_column.desc()

    items = query.order_by(sort_column).offset(skip).limit(limit).all()

    return schemas.ActionItemListResponse(total=total, items=items)


@app.get("/actions/{action_id}", response_model=schemas.ActionItem, tags=["行动项管理"])
def get_action(action_id: int, db: Session = Depends(get_db)):
    db_action = db.query(models.ActionItem).filter(models.ActionItem.id == action_id).first()
    if not db_action:
        raise HTTPException(status_code=404, detail="行动项不存在")
    return db_action


@app.put("/actions/{action_id}", response_model=schemas.ActionItem, tags=["行动项管理"])
def update_action(
    action_id: int,
    action_update: schemas.ActionItemUpdate,
    db: Session = Depends(get_db),
):
    db_action = db.query(models.ActionItem).filter(models.ActionItem.id == action_id).first()
    if not db_action:
        raise HTTPException(status_code=404, detail="行动项不存在")

    update_data = action_update.model_dump(exclude_unset=True)

    if "assignee_id" in update_data:
        db_assignee = db.query(models.Assignee).filter(models.Assignee.id == update_data["assignee_id"]).first()
        if not db_assignee:
            raise HTTPException(status_code=400, detail="指定的负责人不存在")
    if "meeting_id" in update_data and update_data["meeting_id"]:
        db_meeting = db.query(models.Meeting).filter(models.Meeting.id == update_data["meeting_id"]).first()
        if not db_meeting:
            raise HTTPException(status_code=400, detail="指定的会议不存在")
    if "status" in update_data and update_data["status"] == models.ActionStatus.COMPLETED:
        update_data["completed_at"] = datetime.utcnow()

    for key, value in update_data.items():
        setattr(db_action, key, value)

    db.commit()
    db.refresh(db_action)
    return db_action


@app.patch("/actions/{action_id}/status", response_model=schemas.ActionItem, tags=["行动项管理"])
def update_action_status(
    action_id: int,
    status: models.ActionStatus = Query(..., description="新状态"),
    db: Session = Depends(get_db),
):
    db_action = db.query(models.ActionItem).filter(models.ActionItem.id == action_id).first()
    if not db_action:
        raise HTTPException(status_code=404, detail="行动项不存在")
    db_action.status = status
    if status == models.ActionStatus.COMPLETED:
        db_action.completed_at = datetime.utcnow()
    else:
        db_action.completed_at = None
    db.commit()
    db.refresh(db_action)
    return db_action


@app.delete("/actions/{action_id}", tags=["行动项管理"])
def delete_action(action_id: int, db: Session = Depends(get_db)):
    db_action = db.query(models.ActionItem).filter(models.ActionItem.id == action_id).first()
    if not db_action:
        raise HTTPException(status_code=404, detail="行动项不存在")
    db.delete(db_action)
    db.commit()
    return {"message": "删除成功"}


# ==================== 到期提醒 ====================

@app.get("/reminders/", response_model=schemas.ReminderResponse, tags=["到期提醒"])
def get_reminders(
    assignee_id: Optional[int] = Query(None, description="按负责人筛选"),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)

    base_query = db.query(models.ActionItem).filter(
        models.ActionItem.status.notin_([models.ActionStatus.COMPLETED, models.ActionStatus.CANCELLED])
    )
    if assignee_id:
        base_query = base_query.filter(models.ActionItem.assignee_id == assignee_id)

    overdue = base_query.filter(models.ActionItem.due_date < now).order_by(models.ActionItem.due_date.asc()).all()

    due_today = base_query.filter(
        and_(models.ActionItem.due_date >= today_start, models.ActionItem.due_date < today_end)
    ).order_by(models.ActionItem.priority.desc(), models.ActionItem.due_date.asc()).all()

    due_this_week = base_query.filter(
        and_(models.ActionItem.due_date >= today_end, models.ActionItem.due_date < week_end)
    ).order_by(models.ActionItem.due_date.asc(), models.ActionItem.priority.desc()).all()

    return schemas.ReminderResponse(
        overdue=overdue,
        due_today=due_today,
        due_this_week=due_this_week,
    )


@app.get("/stats/summary", tags=["统计"])
def get_stats_summary(db: Session = Depends(get_db)):
    now = datetime.utcnow()

    total = db.query(models.ActionItem).count()
    by_status = {}
    for s in models.ActionStatus:
        by_status[s.value] = db.query(models.ActionItem).filter(models.ActionItem.status == s).count()

    by_priority = {}
    for p in models.Priority:
        by_priority[p.value] = db.query(models.ActionItem).filter(models.ActionItem.priority == p).count()

    overdue_count = db.query(models.ActionItem).filter(
        and_(
            models.ActionItem.due_date < now,
            models.ActionItem.status.notin_([models.ActionStatus.COMPLETED, models.ActionStatus.CANCELLED]),
        )
    ).count()

    assignees_with_counts = (
        db.query(
            models.Assignee.id,
            models.Assignee.name,
            models.Assignee.department,
        )
        .all()
    )

    assignee_stats = []
    for aid, aname, adep in assignees_with_counts:
        pending = db.query(models.ActionItem).filter(
            and_(
                models.ActionItem.assignee_id == aid,
                models.ActionItem.status.notin_([models.ActionStatus.COMPLETED, models.ActionStatus.CANCELLED]),
            )
        ).count()
        completed = db.query(models.ActionItem).filter(
            and_(
                models.ActionItem.assignee_id == aid,
                models.ActionItem.status == models.ActionStatus.COMPLETED,
            )
        ).count()
        overdue = db.query(models.ActionItem).filter(
            and_(
                models.ActionItem.assignee_id == aid,
                models.ActionItem.due_date < now,
                models.ActionItem.status.notin_([models.ActionStatus.COMPLETED, models.ActionStatus.CANCELLED]),
            )
        ).count()
        assignee_stats.append({
            "id": aid,
            "name": aname,
            "department": adep,
            "pending": pending,
            "completed": completed,
            "overdue": overdue,
        })

    return {
        "total": total,
        "by_status": by_status,
        "by_priority": by_priority,
        "overdue": overdue_count,
        "assignees": assignee_stats,
    }
