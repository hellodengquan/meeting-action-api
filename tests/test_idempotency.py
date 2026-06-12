import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
import requests

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import NotificationConfig
import notifier
import models


# ============================================================
# 幂等场景 1：同一 action 当天重复触发只发送一次
# ============================================================

def test_same_action_same_day_dedup_once(db_session, sample_data, temp_log_path):
    """同一 action 同一天多次调用 send_reminders，HTTP 请求只触发一次，第二次被跳过"""
    config = NotificationConfig(
        enabled=True,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-xxx",
        reminder_log_path=temp_log_path,
    )
    now = datetime(2026, 6, 13, 9, 0, 0)
    items = notifier.fetch_upcoming_items(db_session, within_days=7, now=now)
    assert len(items) >= 2

    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.post.return_value = mock_resp

    result1 = notifier.send_reminders(items, config=config, http_session=mock_session, db_session=db_session, now=now)
    assert result1["delivered"] is True
    assert result1["channel"] == "feishu"
    assert result1["count"] == len(items)
    assert result1["skipped_dedup"] == 0
    assert mock_session.post.call_count == 1

    sent_records = db_session.query(models.ReminderSent).all()
    assert len(sent_records) == len(items)
    for rec in sent_records:
        assert rec.sent_date == "2026-06-13"
        assert rec.channel == "feishu"

    result2 = notifier.send_reminders(items, config=config, http_session=mock_session, db_session=db_session, now=now)
    assert result2["delivered"] is False
    assert result2["channel"] == "none"
    assert result2["count"] == 0
    assert result2["skipped_dedup"] == len(items)
    assert mock_session.post.call_count == 1


def test_partial_new_items_mixed_dedup(db_session, sample_data, temp_log_path):
    """当天已发过一部分，新增的行动项仍会正常发送，已发送的被跳过"""
    config = NotificationConfig(
        enabled=True,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-xxx",
        reminder_log_path=temp_log_path,
    )
    now = datetime(2026, 6, 13, 9, 0, 0)
    all_items = notifier.fetch_upcoming_items(db_session, within_days=7, now=now)
    first_batch = all_items[:1]
    rest_batch = all_items[1:]

    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.post.return_value = mock_resp

    notifier.send_reminders(first_batch, config=config, http_session=mock_session, db_session=db_session, now=now)
    assert mock_session.post.call_count == 1

    result = notifier.send_reminders(all_items, config=config, http_session=mock_session, db_session=db_session, now=now)
    assert result["delivered"] is True
    assert result["count"] == len(rest_batch)
    assert result["skipped_dedup"] == len(first_batch)
    assert mock_session.post.call_count == 2

    sent_ids = notifier.fetch_already_sent_action_ids(db_session, now=now)
    assert sent_ids == {item.id for item in all_items}


# ============================================================
# 幂等场景 2：跨天提醒重新发出
# ============================================================

def test_same_action_next_day_resend(db_session, sample_data, temp_log_path):
    """同一 action 隔天再次调用会重新发送（不同 sent_date）"""
    config = NotificationConfig(
        enabled=True,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-xxx",
        reminder_log_path=temp_log_path,
    )
    day1 = datetime(2026, 6, 13, 9, 0, 0)
    day2 = datetime(2026, 6, 14, 9, 0, 0)
    items = notifier.fetch_upcoming_items(db_session, within_days=7, now=day1)

    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.post.return_value = mock_resp

    result1 = notifier.send_reminders(items, config=config, http_session=mock_session, db_session=db_session, now=day1)
    assert result1["delivered"] is True
    assert result1["count"] == len(items)
    assert result1["skipped_dedup"] == 0
    assert mock_session.post.call_count == 1

    day1_sent = db_session.query(models.ReminderSent).filter(models.ReminderSent.sent_date == "2026-06-13").count()
    assert day1_sent == len(items)

    result2 = notifier.send_reminders(items, config=config, http_session=mock_session, db_session=db_session, now=day2)
    assert result2["delivered"] is True
    assert result2["count"] == len(items)
    assert result2["skipped_dedup"] == 0
    assert mock_session.post.call_count == 2

    day2_sent = db_session.query(models.ReminderSent).filter(models.ReminderSent.sent_date == "2026-06-14").count()
    assert day2_sent == len(items)
    total_sent = db_session.query(models.ReminderSent).count()
    assert total_sent == len(items) * 2


# ============================================================
# 幂等场景 3：reminder_sent 表 7 天过期清理
# ============================================================

def test_cleanup_expired_sent_records(db_session, sample_data):
    """超过 7 天的 reminder_sent 记录会被清理，近期的保留"""
    items = sample_data["actions"]
    now = datetime(2026, 6, 13, 9, 0, 0)

    old_records = []
    for i, item in enumerate(items):
        sent_at = now - timedelta(days=8, hours=i)
        old_records.append(models.ReminderSent(
            action_id=item.id,
            sent_date=sent_at.strftime("%Y-%m-%d"),
            sent_at=sent_at,
            channel="feishu",
        ))
    recent_records = []
    for i, item in enumerate(items):
        sent_at = now - timedelta(days=3, hours=i)
        recent_records.append(models.ReminderSent(
            action_id=item.id,
            sent_date=sent_at.strftime("%Y-%m-%d"),
            sent_at=sent_at,
            channel="feishu",
        ))
    db_session.add_all(old_records + recent_records)
    db_session.commit()

    total_before = db_session.query(models.ReminderSent).count()
    assert total_before == len(items) * 2

    deleted = notifier.cleanup_expired_sent_records(db_session, now=now, retention_days=7)
    assert deleted == len(items)

    total_after = db_session.query(models.ReminderSent).count()
    assert total_after == len(items)

    remaining_dates = {r.sent_date for r in db_session.query(models.ReminderSent).all()}
    assert all(d == (now - timedelta(days=3)).strftime("%Y-%m-%d") for d in remaining_dates)


def test_cleanup_does_not_break_when_empty(db_session):
    """空表执行清理不会抛错，返回 0"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    deleted = notifier.cleanup_expired_sent_records(db_session, now=now)
    assert deleted == 0


# ============================================================
# 幂等辅助：发送失败不写幂等记录
# ============================================================

def test_webhook_5xx_does_not_write_sent_record(db_session, sample_data, temp_log_path):
    """webhook 5xx 失败时，不写入 reminder_sent，下次重试仍可发送"""
    config = NotificationConfig(
        enabled=True,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-xxx",
        reminder_log_path=temp_log_path,
    )
    now = datetime(2026, 6, 13, 9, 0, 0)
    items = notifier.fetch_upcoming_items(db_session, within_days=7, now=now)

    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_session.post.return_value = mock_resp

    result = notifier.send_reminders(items, config=config, http_session=mock_session, db_session=db_session, now=now)
    assert result["delivered"] is False
    assert result["reason"] == "webhook_error"

    sent_count = db_session.query(models.ReminderSent).count()
    assert sent_count == 0

    mock_resp.status_code = 200
    result2 = notifier.send_reminders(items, config=config, http_session=mock_session, db_session=db_session, now=now)
    assert result2["delivered"] is True
    assert result2["count"] == len(items)
    assert db_session.query(models.ReminderSent).count() == len(items)
