import logging
import json
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Set

import requests

from config import NotificationConfig, notification_config
from models import ActionItem, ActionStatus, ReminderSent


logger = logging.getLogger("reminder")
logger.setLevel(logging.INFO)

REMINDER_RETENTION_DAYS = 7


def _setup_file_logger(log_path: str) -> logging.Logger:
    file_logger = logging.getLogger(f"reminder.file.{log_path}")
    file_logger.setLevel(logging.INFO)
    file_logger.propagate = False
    if not any(isinstance(h, logging.FileHandler) and h.baseFilename.endswith(log_path) for h in file_logger.handlers):
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        file_logger.addHandler(fh)
    return file_logger


def calculate_days_left(due_date: datetime, now: Optional[datetime] = None) -> int:
    if now is None:
        now = datetime.utcnow()
    delta = due_date.replace(tzinfo=None) - now.replace(tzinfo=None)
    return delta.days


def _describe_status(days_left: int) -> str:
    if days_left < 0:
        return f"已逾期 {-days_left} 天"
    elif days_left == 0:
        return "今日到期"
    else:
        return f"剩余 {days_left} 天"


def build_reminder_lines(items: List[ActionItem]) -> Tuple[List[str], List[dict]]:
    text_lines: List[str] = []
    structured: List[dict] = []

    for item in items:
        assignee_name = item.assignee.name if item.assignee else "未指定"
        assignee_email = item.assignee.email if (item.assignee and item.assignee.email) else None
        days_left = calculate_days_left(item.due_date)
        status_label = _describe_status(days_left)
        priority_label = item.priority.value if item.priority else "medium"

        contact = assignee_email if assignee_email else f"{assignee_name}(无邮箱)"
        line = f"• [{status_label} | {priority_label}] {item.title} — 负责人：{contact}"
        text_lines.append(line)
        structured.append({
            "action_id": item.id,
            "title": item.title,
            "assignee": assignee_name,
            "email": assignee_email,
            "days_left": days_left,
            "due_date": item.due_date.isoformat(),
            "status": item.status.value if item.status else "pending",
            "priority": priority_label,
        })

    return text_lines, structured


def build_feishu_payload(text_lines: List[str]) -> dict:
    header = "📋 会议行动项到期提醒"
    content = header + "\n" + "\n".join(text_lines) if text_lines else header + "\n🎉 今日无即将到期的行动项"
    return {
        "msg_type": "text",
        "content": {"text": content},
    }


def log_reminder_fallback(structured: List[dict], log_path: str, reason: str) -> None:
    file_logger = _setup_file_logger(log_path)
    file_logger.info("飞书提醒降级记录 [原因: %s] — 共 %d 条", reason, len(structured))
    for entry in structured:
        file_logger.info(json.dumps(entry, ensure_ascii=False))


# ============================================================
# 幂等控制
# ============================================================

def _sent_date(now: Optional[datetime] = None) -> str:
    if now is None:
        now = datetime.utcnow()
    return now.strftime("%Y-%m-%d")


def fetch_already_sent_action_ids(db_session, now: Optional[datetime] = None) -> Set[int]:
    sent_date_str = _sent_date(now)
    rows = (
        db_session.query(ReminderSent.action_id)
        .filter(ReminderSent.sent_date == sent_date_str)
        .all()
    )
    return {row[0] for row in rows}


def filter_pending_items(db_session, items: List[ActionItem], now: Optional[datetime] = None) -> List[ActionItem]:
    sent_ids = fetch_already_sent_action_ids(db_session, now=now)
    return [item for item in items if item.id not in sent_ids]


def mark_items_as_sent(db_session, items: List[ActionItem], channel: str = "feishu", now: Optional[datetime] = None) -> None:
    sent_date_str = _sent_date(now)
    sent_at = now or datetime.utcnow()
    existing_ids = fetch_already_sent_action_ids(db_session, now=now)
    new_records = [
        ReminderSent(action_id=item.id, sent_date=sent_date_str, sent_at=sent_at, channel=channel)
        for item in items
        if item.id not in existing_ids
    ]
    if new_records:
        db_session.bulk_save_objects(new_records)
        db_session.commit()


def cleanup_expired_sent_records(db_session, now: Optional[datetime] = None, retention_days: int = REMINDER_RETENTION_DAYS) -> int:
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(days=retention_days)
    deleted = (
        db_session.query(ReminderSent)
        .filter(ReminderSent.sent_at < cutoff)
        .delete(synchronize_session=False)
    )
    db_session.commit()
    logger.info("清理过期提醒记录 %d 条（早于 %s）", deleted, cutoff.isoformat())
    return deleted


# ============================================================
# 幂等统计（可观测性）
# ============================================================

def get_idempotency_summary(db_session, period_days: int = 7, now: Optional[datetime] = None) -> dict:
    from sqlalchemy import func

    if now is None:
        now = datetime.utcnow()

    today_start = datetime(now.year, now.month, now.day)
    start_date = today_start - timedelta(days=period_days - 1)
    end_date = today_start

    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")

    daily_counts = (
        db_session.query(
            ReminderSent.sent_date,
            func.count(ReminderSent.id).label("cnt"),
        )
        .filter(
            ReminderSent.sent_date >= start_date_str,
            ReminderSent.sent_date <= end_date_str,
        )
        .group_by(ReminderSent.sent_date)
        .order_by(ReminderSent.sent_date.asc())
        .all()
    )

    daily_skip_map = {date: cnt for date, cnt in daily_counts}

    full_daily = []
    for i in range(period_days):
        d = start_date + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        full_daily.append({"date": d_str, "skipped_count": daily_skip_map.get(d_str, 0)})

    action_counts = (
        db_session.query(
            ReminderSent.action_id,
            func.count(ReminderSent.id).label("cnt"),
        )
        .filter(
            ReminderSent.sent_date >= start_date_str,
            ReminderSent.sent_date <= end_date_str,
        )
        .group_by(ReminderSent.action_id)
        .order_by(func.count(ReminderSent.id).desc())
        .limit(1)
        .all()
    )

    most_skipped_action_id = None
    most_skipped_action_count = 0
    if action_counts:
        most_skipped_action_id, most_skipped_action_count = action_counts[0]

    total_skipped = sum(item["skipped_count"] for item in full_daily)

    return {
        "period_days": period_days,
        "total_skipped": total_skipped,
        "daily_skip": full_daily,
        "most_skipped_action_id": most_skipped_action_id,
        "most_skipped_action_count": most_skipped_action_count,
        "data_start_date": start_date_str,
        "data_end_date": end_date_str,
    }


def get_idempotency_detail(db_session, action_id: int, period_days: int = 7, now: Optional[datetime] = None) -> dict:
    from sqlalchemy import func

    if now is None:
        now = datetime.utcnow()

    today_start = datetime(now.year, now.month, now.day)
    start_date = today_start - timedelta(days=period_days - 1)
    end_date = today_start
    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")

    records = (
        db_session.query(
            ReminderSent.sent_date,
            func.count(ReminderSent.id).label("cnt"),
            func.min(ReminderSent.sent_at).label("first_sent"),
        )
        .filter(
            ReminderSent.action_id == action_id,
            ReminderSent.sent_date >= start_date_str,
            ReminderSent.sent_date <= end_date_str,
        )
        .group_by(ReminderSent.sent_date)
        .order_by(ReminderSent.sent_date.asc())
        .all()
    )

    rec_map = {}
    sent_dates_in_period = []
    for sent_date_str, cnt, first_sent in records:
        rec_map[sent_date_str] = {"count": cnt, "first_sent": first_sent}
        sent_dates_in_period.append(sent_date_str)

    has_data = len(sent_dates_in_period) > 0

    daily_details = []
    for i in range(period_days):
        d = start_date + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        day_rec = rec_map.get(d_str)
        if day_rec:
            first_sent_str = day_rec["first_sent"].isoformat() if day_rec["first_sent"] else None
            daily_details.append({
                "date": d_str,
                "first_sent_at": first_sent_str,
                "skip_count": day_rec["count"],
            })
        else:
            daily_details.append({
                "date": d_str,
                "first_sent_at": None,
                "skip_count": 0,
            })

    total_skips = sum(d["skip_count"] for d in daily_details)

    avg_interval: Optional[float] = None
    if has_data:
        sent_date_objs = sorted([
            datetime.strptime(ds, "%Y-%m-%d")
            for ds in sent_dates_in_period
        ])
        if len(sent_date_objs) >= 2:
            intervals = [
                (sent_date_objs[i + 1] - sent_date_objs[i]).days
                for i in range(len(sent_date_objs) - 1)
            ]
            avg_interval = round(sum(intervals) / len(intervals), 2)
        else:
            avg_interval = None

    return {
        "action_id": action_id,
        "period_days": period_days,
        "has_data": has_data,
        "total_skips_in_period": total_skips,
        "average_skip_interval_days": avg_interval,
        "daily_details": daily_details,
    }


# ============================================================
# 主入口（含幂等控制）
# ============================================================

def send_reminders(
    items: List[ActionItem],
    config: Optional[NotificationConfig] = None,
    http_session: Optional[requests.Session] = None,
    db_session=None,
    now: Optional[datetime] = None,
) -> dict:
    if config is None:
        config = notification_config

    if now is None:
        now = datetime.utcnow()

    original_count = len(items)

    if db_session is not None:
        items = filter_pending_items(db_session, items, now=now)

    text_lines, structured = build_reminder_lines(items)
    skipped_count = original_count - len(items)

    if not items:
        return {
            "delivered": False,
            "channel": "none",
            "count": 0,
            "skipped_dedup": skipped_count,
            "message": "无需要提醒的行动项",
        }

    if not config.enabled:
        log_reminder_fallback(structured, config.reminder_log_path, "通知未启用")
        return {
            "delivered": False,
            "channel": "log",
            "count": len(structured),
            "skipped_dedup": skipped_count,
            "reason": "notification_disabled",
        }

    if not config.webhook_url:
        log_reminder_fallback(structured, config.reminder_log_path, "webhook URL 未配置")
        return {
            "delivered": False,
            "channel": "log",
            "count": len(structured),
            "skipped_dedup": skipped_count,
            "reason": "webhook_missing",
        }

    payload = build_feishu_payload(text_lines)
    session = http_session or requests.Session()

    try:
        response = session.post(config.webhook_url, json=payload, timeout=10)
        if 200 <= response.status_code < 300:
            if db_session is not None:
                mark_items_as_sent(db_session, items, channel="feishu", now=now)
            return {
                "delivered": True,
                "channel": "feishu",
                "count": len(structured),
                "skipped_dedup": skipped_count,
                "status_code": response.status_code,
            }
        else:
            log_reminder_fallback(structured, config.reminder_log_path, f"webhook 返回 {response.status_code}")
            return {
                "delivered": False,
                "channel": "log",
                "count": len(structured),
                "skipped_dedup": skipped_count,
                "reason": "webhook_error",
                "status_code": response.status_code,
            }
    except requests.RequestException as exc:
        log_reminder_fallback(structured, config.reminder_log_path, f"webhook 请求异常: {exc}")
        return {
            "delivered": False,
            "channel": "log",
            "count": len(structured),
            "skipped_dedup": skipped_count,
            "reason": f"request_exception:{type(exc).__name__}",
        }


def fetch_upcoming_items(db_session, within_days: int = 7, now: Optional[datetime] = None) -> List[ActionItem]:
    if now is None:
        now = datetime.utcnow()
    horizon = now + timedelta(days=within_days)
    return (
        db_session.query(ActionItem)
        .filter(
            ActionItem.status.notin_([ActionStatus.COMPLETED, ActionStatus.CANCELLED]),
            ActionItem.due_date <= horizon,
        )
        .order_by(ActionItem.due_date.asc(), ActionItem.priority.desc())
        .all()
    )
