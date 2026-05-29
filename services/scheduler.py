import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from config import settings
from services.crawler_service import run_full_crawl

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler = None


def start_scheduler(keywords=None):
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    _scheduler.add_job(
        func=lambda: run_full_crawl(keywords),
        trigger=IntervalTrigger(minutes=settings.crawl_interval_minutes),
        id="full_crawl",
        name="定時爬取 PTT + Dcard",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info(f"排程已啟動，每 {settings.crawl_interval_minutes} 分鐘執行一次")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("排程已停止")


def get_scheduler_status() -> dict:
    global _scheduler
    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time),
        })
    return {"running": True, "jobs": jobs}
