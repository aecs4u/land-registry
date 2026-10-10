"""Run and track background refreshes of cadastral parcel flags."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATHS = {
    "has_visura": _PROJECT_ROOT / "scripts" / "refresh_cadastral_parcel_visura.py",
    "is_auction_sale": _PROJECT_ROOT / "scripts" / "refresh_cadastral_parcel_auction.py",
}
_jobs: dict[str, dict[str, Any]] = {}
_tasks: dict[str, asyncio.Task] = {}
_refresh_lock = asyncio.Lock()
_retention_seconds = 6 * 60 * 60
_max_jobs = 100


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        key: job.get(key)
        for key in ("job_id", "flag", "status", "created_at", "started_at", "finished_at", "result", "error")
    }


def _prune_jobs() -> None:
    now = time.monotonic()
    completed = [
        (job_id, job)
        for job_id, job in _jobs.items()
        if job["status"] in {"completed", "failed"}
    ]
    for job_id, job in completed:
        if now - job["created_monotonic"] > _retention_seconds:
            _jobs.pop(job_id, None)
            _tasks.pop(job_id, None)
    completed = [
        (job_id, job)
        for job_id, job in _jobs.items()
        if job["status"] in {"completed", "failed"}
    ]
    if len(_jobs) > _max_jobs:
        excess = len(_jobs) - _max_jobs
        for job_id, _ in sorted(completed, key=lambda item: item[1]["created_monotonic"])[:excess]:
            _jobs.pop(job_id, None)
            _tasks.pop(job_id, None)


def get_parcel_flag_refresh(job_id: str) -> dict[str, Any] | None:
    job = _jobs.get(job_id)
    return _public_job(job) if job else None


def _load_refresh_script(flag: str):
    path = _SCRIPT_PATHS[flag]
    if not path.is_file():
        raise RuntimeError(f"Parcel flag refresh script is missing: {path.name}")
    module_name = f"_land_registry_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load parcel flag refresh script: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


async def _run_refresh(job_id: str) -> None:
    job = _jobs[job_id]
    try:
        async with _refresh_lock:
            job["status"] = "running"
            job["started_at"] = _utc_now()
            module = _load_refresh_script(job["flag"])
            if job["flag"] == "has_visura":
                result = await module.run(batch_size=1000, dry_run=False)
            else:
                result = await module.run(batch_size=5000, apply=True)
            job["result"] = result or {}
            job["status"] = "completed"
    except Exception as exc:
        logger.exception("Cadastral parcel flag refresh failed (flag=%s, job=%s)", job["flag"], job_id)
        job["error"] = str(exc) or exc.__class__.__name__
        job["status"] = "failed"
    finally:
        job["finished_at"] = _utc_now()


async def start_parcel_flag_refresh(flag: str) -> dict[str, Any]:
    if flag not in _SCRIPT_PATHS:
        raise ValueError(f"Unsupported cadastral parcel flag: {flag}")
    _prune_jobs()
    for job in reversed(list(_jobs.values())):
        if job["flag"] == flag and job["status"] in {"queued", "running"}:
            return _public_job(job)
    job_id = str(uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "flag": flag,
        "status": "queued",
        "created_at": _utc_now(),
        "created_monotonic": time.monotonic(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    }
    task = asyncio.create_task(_run_refresh(job_id), name=f"parcel-flag-refresh-{flag}-{job_id}")
    _tasks[job_id] = task
    return _public_job(_jobs[job_id])
