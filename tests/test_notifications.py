import json
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


def _read_log_lines(log_path: str) -> list:
    if not os.path.exists(log_path):
        return []
    with open(log_path, "r", encoding="utf-8") as fp:
        return fp.readlines()


# ============================================================
# 场景 1：webhook 启用/关闭
# ============================================================

def test_webhook_enabled_and_success(db_session, sample_data, temp_log_path):
    """场景1-1: webhook 启用且返回 200，消息走飞书通道，不写日志"""
    config = NotificationConfig(
        enabled=True,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-xxx",
        reminder_log_path=temp_log_path,
    )
    items = notifier.fetch_upcoming_items(db_session, within_days=7)
    assert len(items) >= 2

    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.post.return_value = mock_resp

    result = notifier.send_reminders(items, config=config, http_session=mock_session)

    assert result["delivered"] is True
    assert result["channel"] == "feishu"
    assert result["count"] == len(items)
    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert call_args[0][0] == config.webhook_url
    payload = call_args[1]["json"]
    assert payload["msg_type"] == "text"
    assert "行动项到期提醒" in payload["content"]["text"]
    for item in items:
        assert item.title in payload["content"]["text"]
    lines = _read_log_lines(temp_log_path)
    assert len(lines) == 0


def test_webhook_disabled_should_fallback_to_log(db_session, sample_data, temp_log_path):
    """场景1-2: webhook 关闭，直接降级写入 reminder.log，不发起 HTTP 请求"""
    config = NotificationConfig(
        enabled=False,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-xxx",
        reminder_log_path=temp_log_path,
    )
    items = notifier.fetch_upcoming_items(db_session, within_days=7)

    mock_session = MagicMock(spec=requests.Session)

    result = notifier.send_reminders(items, config=config, http_session=mock_session)

    assert result["delivered"] is False
    assert result["channel"] == "log"
    assert result["reason"] == "notification_disabled"
    assert result["count"] == len(items)
    mock_session.post.assert_not_called()
    lines = _read_log_lines(temp_log_path)
    assert len(lines) >= 1
    summary = lines[0]
    assert "通知未启用" in summary
    assert f"共 {len(items)} 条" in summary
    json_lines = [ln for ln in lines[1:] if ln.strip()]
    assert len(json_lines) == len(items)
    for entry_line in json_lines:
        json_start = entry_line.find("{")
        assert json_start >= 0, f"未找到 JSON 内容: {entry_line}"
        entry = json.loads(entry_line[json_start:])
        assert "action_id" in entry
        assert "title" in entry
        assert "assignee" in entry


# ============================================================
# 场景 2：负责人邮箱缺失时的 fallback 处理
# ============================================================

def test_assignee_missing_email_fallback_in_payload(db_session, sample_data):
    """场景2: 负责人邮箱缺失时，提醒内容展示 '姓名(无邮箱)' 而非报错"""
    items = notifier.fetch_upcoming_items(db_session, within_days=7)
    text_lines, structured = notifier.build_reminder_lines(items)

    no_email_item = next(i for i in items if not i.assignee.email)
    assert no_email_item is not None

    no_email_line = next(ln for ln in text_lines if no_email_item.title in ln)
    assert "(无邮箱)" in no_email_line
    assert no_email_item.assignee.name in no_email_line

    has_email_item = next(i for i in items if i.assignee.email)
    has_email_line = next(ln for ln in text_lines if has_email_item.title in ln)
    assert has_email_item.assignee.email in has_email_line

    no_email_entry = next(e for e in structured if e["title"] == no_email_item.title)
    assert no_email_entry["email"] is None
    has_email_entry = next(e for e in structured if e["title"] == has_email_item.title)
    assert has_email_entry["email"] == "zhangsan@example.com"

    payload = notifier.build_feishu_payload(text_lines)
    assert "(无邮箱)" in payload["content"]["text"]
    assert "zhangsan@example.com" in payload["content"]["text"]


# ============================================================
# 场景 3：webhook 返回 5xx 时降级到日志记录
# ============================================================

def test_webhook_5xx_response_fallback_to_log(db_session, sample_data, temp_log_path):
    """场景3: webhook 返回 5xx，降级写 reminder.log，记录返回状态码"""
    config = NotificationConfig(
        enabled=True,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-xxx",
        reminder_log_path=temp_log_path,
    )
    items = notifier.fetch_upcoming_items(db_session, within_days=7)

    mock_session = MagicMock(spec=requests.Session)
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_session.post.return_value = mock_resp

    result = notifier.send_reminders(items, config=config, http_session=mock_session)

    assert result["delivered"] is False
    assert result["channel"] == "log"
    assert result["reason"] == "webhook_error"
    assert result["status_code"] == 503
    assert result["count"] == len(items)
    mock_session.post.assert_called_once()

    lines = _read_log_lines(temp_log_path)
    assert len(lines) >= 1
    summary = lines[0]
    assert "webhook 返回 503" in summary
    json_lines = [ln for ln in lines[1:] if ln.strip()]
    assert len(json_lines) == len(items)
    for entry_line in json_lines:
        json_start = entry_line.find("{")
        assert json_start >= 0
        entry = json.loads(entry_line[json_start:])
        assert "action_id" in entry


def test_webhook_connection_error_fallback_to_log(db_session, sample_data, temp_log_path):
    """场景3 扩展: 网络异常(连接错误)同样降级写日志"""
    config = NotificationConfig(
        enabled=True,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-xxx",
        reminder_log_path=temp_log_path,
    )
    items = notifier.fetch_upcoming_items(db_session, within_days=7)

    mock_session = MagicMock(spec=requests.Session)
    mock_session.post.side_effect = requests.ConnectionError("DNS 解析失败")

    result = notifier.send_reminders(items, config=config, http_session=mock_session)

    assert result["delivered"] is False
    assert result["channel"] == "log"
    assert "request_exception" in result["reason"]
    lines = _read_log_lines(temp_log_path)
    assert len(lines) >= 1
    assert "webhook 请求异常" in lines[0]


# ============================================================
# 补充：剩余天数计算
# ============================================================

def test_calculate_days_left():
    now = datetime(2026, 6, 13, 10, 0, 0)
    assert notifier.calculate_days_left(now + timedelta(hours=5), now) == 0
    assert notifier.calculate_days_left(now + timedelta(days=2), now) == 2
    assert notifier.calculate_days_left(now - timedelta(days=1, hours=1), now) == -2
