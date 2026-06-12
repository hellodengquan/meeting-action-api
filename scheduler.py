import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import SessionLocal, notification_config
import notifier


logger = logging.getLogger("scheduler")
logging.basicConfig(level=logging.INFO)


def run_reminder_scan() -> dict:
    logger.info("开始执行每日到期提醒扫描...")
    db = SessionLocal()
    try:
        items = notifier.fetch_upcoming_items(db, within_days=7)
        logger.info("扫描到 %d 条即将到期的行动项", len(items))
        result = notifier.send_reminders(items)
        logger.info("提醒执行结果: %s", result)
        return result
    except Exception as exc:
        logger.exception("提醒扫描任务异常: %s", exc)
        return {"delivered": False, "channel": "none", "error": str(exc)}
    finally:
        db.close()


_scheduler: Optional[BackgroundScheduler] = None


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    trigger = CronTrigger(
        hour=notification_config.scan_hour,
        minute=notification_config.scan_minute,
    )
    _scheduler.add_job(
        run_reminder_scan,
        trigger=trigger,
        id="daily_reminder_scan",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "定时任务已启动，每日 %02d:%02d 执行提醒扫描（当前通知启用=%s）",
        notification_config.scan_hour,
        notification_config.scan_minute,
        notification_config.enabled,
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("定时任务已停止")
