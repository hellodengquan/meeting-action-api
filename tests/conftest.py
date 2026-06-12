import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Base
import models


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def sample_data(db_session):
    a1 = models.Assignee(name="有邮箱的张三", email="zhangsan@example.com", department="研发部")
    a2 = models.Assignee(name="无邮箱的李四", email=None, department="产品部")
    db_session.add_all([a1, a2])
    db_session.flush()

    now = datetime.utcnow()
    items = [
        models.ActionItem(
            title="完成接口文档",
            assignee_id=a1.id,
            status=models.ActionStatus.PENDING,
            priority=models.Priority.HIGH,
            due_date=now + timedelta(days=1),
        ),
        models.ActionItem(
            title="评审原型设计",
            assignee_id=a2.id,
            status=models.ActionStatus.IN_PROGRESS,
            priority=models.Priority.MEDIUM,
            due_date=now + timedelta(days=3),
        ),
        models.ActionItem(
            title="已经逾期的任务",
            assignee_id=a1.id,
            status=models.ActionStatus.PENDING,
            priority=models.Priority.URGENT,
            due_date=now - timedelta(days=2),
        ),
    ]
    db_session.add_all(items)
    db_session.commit()
    return {"assignees": [a1, a2], "actions": items}


@pytest.fixture
def temp_log_path(tmp_path):
    return str(tmp_path / "reminder_test.log")
