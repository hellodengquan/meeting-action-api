import os
from datetime import datetime, timedelta

import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notifier
import models


# ============================================================
# 场景 1：对昨日已发送的 action，当天应计入跳过
# ============================================================

def test_yesterday_sent_action_counts_as_skipped(db_session, sample_data):
    """昨日推送成功的 action，在幂等统计中应计入昨日的跳过数量"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    yesterday = now - timedelta(days=1)

    action_1 = sample_data["actions"][0]
    action_2 = sample_data["actions"][1]

    db_session.add(models.ReminderSent(
        action_id=action_1.id,
        sent_date=yesterday.strftime("%Y-%m-%d"),
        sent_at=yesterday,
        channel="feishu",
    ))
    db_session.add(models.ReminderSent(
        action_id=action_2.id,
        sent_date=yesterday.strftime("%Y-%m-%d"),
        sent_at=yesterday,
        channel="feishu",
    ))
    db_session.commit()

    summary = notifier.get_idempotency_summary(db_session, period_days=7, now=now)

    assert summary["total_skipped"] == 2
    assert summary["period_days"] == 7
    assert summary["data_start_date"] == "2026-06-07"
    assert summary["data_end_date"] == "2026-06-13"

    daily_map = {d["date"]: d["skipped_count"] for d in summary["daily_skip"]}
    assert daily_map.get("2026-06-12") == 2
    assert daily_map.get("2026-06-13") == 0

    assert summary["most_skipped_action_id"] in (action_1.id, action_2.id)
    assert summary["most_skipped_action_count"] == 1

    assert len(summary["daily_skip"]) == 7


# ============================================================
# 场景 2：对未发送的 action，不计入跳过
# ============================================================

def test_unsent_action_not_counted_as_skipped(db_session, sample_data):
    """从未推送过的 action，不会出现在幂等统计跳过计数中"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    yesterday = now - timedelta(days=1)

    action_1 = sample_data["actions"][0]

    db_session.add(models.ReminderSent(
        action_id=action_1.id,
        sent_date=yesterday.strftime("%Y-%m-%d"),
        sent_at=yesterday,
        channel="feishu",
    ))
    db_session.commit()

    summary = notifier.get_idempotency_summary(db_session, period_days=7, now=now)

    assert summary["total_skipped"] == 1
    daily_map = {d["date"]: d["skipped_count"] for d in summary["daily_skip"]}
    assert daily_map.get("2026-06-12") == 1

    action_2_id = sample_data["actions"][1].id
    action_3_id = sample_data["actions"][2].id
    assert summary["most_skipped_action_id"] == action_1.id
    assert summary["most_skipped_action_count"] == 1
    assert summary["most_skipped_action_id"] not in (action_2_id, action_3_id)


def test_no_data_returns_zero_counts(db_session):
    """空表时总跳过为 0，most_skipped 为 None"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    summary = notifier.get_idempotency_summary(db_session, period_days=7, now=now)

    assert summary["total_skipped"] == 0
    assert summary["most_skipped_action_id"] is None
    assert summary["most_skipped_action_count"] == 0
    assert len(summary["daily_skip"]) == 7
    for d in summary["daily_skip"]:
        assert d["skipped_count"] == 0


# ============================================================
# 场景 3：过期数据不应出现在结果中
# ============================================================

def test_expired_records_not_included(db_session, sample_data):
    """超过 7 天的幂等记录不会被统计到最近 7 天的结果中"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    action_1 = sample_data["actions"][0]
    action_2 = sample_data["actions"][1]

    day_before_yesterday = now - timedelta(days=2)
    eight_days_ago = now - timedelta(days=8)

    db_session.add(models.ReminderSent(
        action_id=action_1.id,
        sent_date=day_before_yesterday.strftime("%Y-%m-%d"),
        sent_at=day_before_yesterday,
        channel="feishu",
    ))
    db_session.add(models.ReminderSent(
        action_id=action_1.id,
        sent_date=eight_days_ago.strftime("%Y-%m-%d"),
        sent_at=eight_days_ago,
        channel="feishu",
    ))
    db_session.add(models.ReminderSent(
        action_id=action_2.id,
        sent_date=eight_days_ago.strftime("%Y-%m-%d"),
        sent_at=eight_days_ago,
        channel="feishu",
    ))
    db_session.commit()

    summary = notifier.get_idempotency_summary(db_session, period_days=7, now=now)

    assert summary["total_skipped"] == 1

    daily_map = {d["date"]: d["skipped_count"] for d in summary["daily_skip"]}
    assert daily_map.get("2026-06-11") == 1
    assert "2026-06-05" not in daily_map

    assert summary["most_skipped_action_id"] == action_1.id
    assert summary["most_skipped_action_count"] == 1

    assert summary["data_start_date"] == "2026-06-07"
    assert summary["data_end_date"] == "2026-06-13"


def test_after_cleanup_expired_records_gone(db_session, sample_data):
    """调用 cleanup 后，过期记录被物理删除，统计结果中不含它们"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    action_1 = sample_data["actions"][0]

    day_before_yesterday = now - timedelta(days=2)
    eight_days_ago = now - timedelta(days=8)

    db_session.add_all([
        models.ReminderSent(
            action_id=action_1.id,
            sent_date=day_before_yesterday.strftime("%Y-%m-%d"),
            sent_at=day_before_yesterday,
            channel="feishu",
        ),
        models.ReminderSent(
            action_id=action_1.id,
            sent_date=eight_days_ago.strftime("%Y-%m-%d"),
            sent_at=eight_days_ago,
            channel="feishu",
        ),
    ])
    db_session.commit()

    summary_before = notifier.get_idempotency_summary(db_session, period_days=7, now=now)
    assert summary_before["total_skipped"] == 1

    deleted = notifier.cleanup_expired_sent_records(db_session, now=now, retention_days=7)
    assert deleted == 1

    summary_after = notifier.get_idempotency_summary(db_session, period_days=7, now=now)
    assert summary_after["total_skipped"] == 1

    all_records = db_session.query(models.ReminderSent).all()
    assert len(all_records) == 1
    assert all_records[0].sent_date == day_before_yesterday.strftime("%Y-%m-%d")


# ============================================================
# 补充：most_skipped 统计正确
# ============================================================

def test_most_skipped_action_across_days(db_session, sample_data):
    """同一个 action 跨多天推送，应正确累计为跳过次数最多的"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    action_1 = sample_data["actions"][0]
    action_2 = sample_data["actions"][1]

    for days_ago in [1, 2, 3]:
        d = now - timedelta(days=days_ago)
        db_session.add(models.ReminderSent(
            action_id=action_1.id,
            sent_date=d.strftime("%Y-%m-%d"),
            sent_at=d,
            channel="feishu",
        ))

    d = now - timedelta(days=1)
    db_session.add(models.ReminderSent(
        action_id=action_2.id,
        sent_date=d.strftime("%Y-%m-%d"),
        sent_at=d,
        channel="feishu",
    ))
    db_session.commit()

    summary = notifier.get_idempotency_summary(db_session, period_days=7, now=now)
    assert summary["most_skipped_action_id"] == action_1.id
    assert summary["most_skipped_action_count"] == 3
    assert summary["total_skipped"] == 4
