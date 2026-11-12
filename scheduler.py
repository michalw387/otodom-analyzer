"""Background scheduling (APScheduler) of Otodom scans – the scan itself lives in services/scanner.py."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

import database
from services import scanner

log = logging.getLogger(__name__)

SCAN_JOB_ID = "otodom-scan"
MANUAL_SCAN_JOB_ID = "otodom-scan-manual"

_scan_lock = threading.Lock()  # one scan at a time, whether scheduled or manual
_scheduler: BackgroundScheduler | None = None
_scheduler_lock = threading.Lock()


def ensure_started() -> BackgroundScheduler:
    """Start the scheduler once per process – Streamlit re-runs app.py on every interaction."""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            database.close_interrupted_scans()
            interval = database.get_settings()["scan_interval_minutes"]
            scheduler = BackgroundScheduler(
                job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600}
            )
            scheduler.add_job(run_scan, "interval", minutes=interval, id=SCAN_JOB_ID)
            scheduler.start()
            _scheduler = scheduler
        return _scheduler


def reschedule(interval_minutes: int) -> None:
    ensure_started().reschedule_job(SCAN_JOB_ID, trigger="interval", minutes=interval_minutes)


def trigger_scan_now() -> None:
    ensure_started().add_job(run_scan, id=MANUAL_SCAN_JOB_ID, replace_existing=True)


def next_scan_time() -> datetime | None:
    job = ensure_started().get_job(SCAN_JOB_ID)
    return job.next_run_time if job else None


def is_scan_running() -> bool:
    """True while a scan runs or a manual scan is queued (so the UI reacts right after the click)."""
    return _scan_lock.locked() or ensure_started().get_job(MANUAL_SCAN_JOB_ID) is not None


def run_scan() -> None:
    if not _scan_lock.acquire(blocking=False):
        log.info("Scan already in progress – skipping")
        return
    try:
        scanner.run_scan_once()
    finally:
        _scan_lock.release()
