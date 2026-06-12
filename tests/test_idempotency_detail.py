import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from config import Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import notifier
import models
import main


# ============================================================
# 场景 1：近7天有发送的 action，返回完整时间序列
# ============================================================

def test_action_within_period_returns_full_timeseries(db_session, sample_data):
    """近 N 天内有发送记录，返回完整时间序列，含首次发送时间、当天跳过次数和平均间隔"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    action = sample_data["actions"][0]

    days_ago_list = [6, 4, 2, 0]
    for days_ago in days_ago_list:
        sent_at_t = now - timedelta(days=days_ago, hours=3, minutes=days_ago * 2)
        db_session.add(models.ReminderSent(
            action_id=action.id,
            sent_date=sent_at_t.strftime("%Y-%m-%d"),
            sent_at=sent_at_t,
            channel="feishu",
        ))
    db_session.commit()

    detail = notifier.get_idempotency_detail(db_session, action_id=action.id, period_days=7, now=now)

    assert detail["action_id"] == action.id
    assert detail["period_days"] == 7
    assert detail["has_data"] is True
    assert detail["total_skips_in_period"] == 4

    assert len(detail["daily_details"]) == 7
    daily_map = {d["date"]: d for d in detail["daily_details"]}

    for days_ago in days_ago_list:
        d_str = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        assert daily_map[d_str]["skip_count"] == 1
        assert daily_map[d_str]["first_sent_at"] is not None
        assert d_str in daily_map[d_str]["first_sent_at"]

    gaps = [2, 2, 2]
    expected_avg = round(sum(gaps) / len(gaps), 2)
    assert detail["average_skip_interval_days"] == expected_avg


def test_average_interval_none_when_only_one_day(db_session, sample_data):
    """只有 1 天有记录，平均间隔为 None"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    action = sample_data["actions"][0]
    sent_at = now - timedelta(days=3, hours=3)
    db_session.add(models.ReminderSent(
        action_id=action.id,
        sent_date=sent_at.strftime("%Y-%m-%d"),
        sent_at=sent_at,
        channel="feishu",
    ))
    db_session.commit()

    detail = notifier.get_idempotency_detail(db_session, action_id=action.id, period_days=7, now=now)

    assert detail["has_data"] is True
    assert detail["total_skips_in_period"] == 1
    assert detail["average_skip_interval_days"] is None


# ============================================================
# 场景 2：近7天无发送的 action，返回空数组（skip_count 全部为 0）
# ============================================================

def test_action_without_records_returns_zero_counts(db_session, sample_data):
    """近 N 天内无发送记录，返回的时间序列中各天 skip_count 全部为 0，has_data=False"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    action = sample_data["actions"][0]

    db_session.add(models.ReminderSent(
        action_id=action.id,
        sent_date=(now - timedelta(days=10)).strftime("%Y-%m-%d"),
        sent_at=now - timedelta(days=10),
        channel="feishu",
    ))
    db_session.commit()

    detail = notifier.get_idempotency_detail(db_session, action_id=action.id, period_days=7, now=now)

    assert detail["action_id"] == action.id
    assert detail["period_days"] == 7
    assert detail["has_data"] is False
    assert detail["total_skips_in_period"] == 0
    assert detail["average_skip_interval_days"] is None

    assert len(detail["daily_details"]) == 7
    for d in detail["daily_details"]:
        assert d["skip_count"] == 0
        assert d["first_sent_at"] is None


def test_action_with_no_records_at_all(db_session, sample_data):
    """完全没记录的 action 也返回全 0 序列"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    action = sample_data["actions"][0]

    detail = notifier.get_idempotency_detail(db_session, action_id=action.id, period_days=7, now=now)

    assert detail["has_data"] is False
    assert detail["total_skips_in_period"] == 0
    assert all(d["skip_count"] == 0 for d in detail["daily_details"])


# ============================================================
# 场景 3：days 超过 30 天，返回 400 错误
# ============================================================

@pytest.fixture
def test_client_with_db(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app = main.app
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_days_over_30_returns_400(test_client_with_db):
    """days > 30 时应返回 400"""
    response = test_client_with_db.get("/reminders/idempotency-detail", params={"action_id": 1, "days": 31})
    assert response.status_code == 400
    assert "30" in response.json()["detail"]


def test_days_equals_31_via_notifier(test_client_with_db, sample_data):
    """days=31 返回 400，边界值"""
    resp = test_client_with_db.get("/reminders/idempotency-detail", params={"action_id": 1, "days": 31})
    assert resp.status_code == 400


def test_days_0_returns_400(test_client_with_db):
    """days=0 返回 400"""
    response = test_client_with_db.get("/reminders/idempotency-detail", params={"action_id": 1, "days": 0})
    assert response.status_code == 400


def test_days_30_is_allowed(test_client_with_db, sample_data):
    """days=30 是合法上限，返回 200"""
    action = sample_data["actions"][0]
    response = test_client_with_db.get(
        "/reminders/idempotency-detail",
        params={"action_id": action.id, "days": 30},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["period_days"] == 30
    assert len(data["daily_details"]) == 30


def test_days_1_is_allowed(test_client_with_db, sample_data):
    """days=1 是合法下限，返回 200"""
    action = sample_data["actions"][0]
    response = test_client_with_db.get(
        "/reminders/idempotency-detail",
        params={"action_id": action.id, "days": 1},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["period_days"] == 1
    assert len(data["daily_details"]) == 1


def test_default_days_is_7(test_client_with_db, sample_data):
    """不传 days 参数，默认 7"""
    action = sample_data["actions"][0]
    response = test_client_with_db.get(
        "/reminders/idempotency-detail",
        params={"action_id": action.id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["period_days"] == 7
    assert len(data["daily_details"]) == 7


# ============================================================
# 补充：过期数据不出现在详情中
# ============================================================

def test_expired_records_outside_period_ignored(db_session, sample_data):
    """周期外的旧记录不计入详情统计"""
    now = datetime(2026, 6, 13, 9, 0, 0)
    action = sample_data["actions"][0]

    db_session.add(models.ReminderSent(
        action_id=action.id,
        sent_date=(now - timedelta(days=2)).strftime("%Y-%m-%d"),
        sent_at=now - timedelta(days=2),
        channel="feishu",
    ))
    for days_ago in [8, 10, 15]:
        db_session.add(models.ReminderSent(
            action_id=action.id,
            sent_date=(now - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
            sent_at=now - timedelta(days=days_ago),
            channel="feishu",
        ))
    db_session.commit()

    detail = notifier.get_idempotency_detail(db_session, action_id=action.id, period_days=7, now=now)

    assert detail["has_data"] is True
    assert detail["total_skips_in_period"] == 1
    assert detail["average_skip_interval_days"] is None
    daily_map = {d["date"]: d for d in detail["daily_details"]}
    target = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    assert daily_map[target]["skip_count"] == 1
