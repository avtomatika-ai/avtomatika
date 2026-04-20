# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import asyncio
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.scheduler import Scheduler
from avtomatika.scheduler_config_loader import ScheduledJobConfig, load_schedules_from_file
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def engine(storage, config):
    engine = OrchestratorEngine(storage, config)
    engine.blueprints = {"test_bp": MagicMock(), "interval_bp": MagicMock()}
    engine.blueprints["test_bp"].name = "test_bp"
    engine.blueprints["interval_bp"].name = "interval_bp"
    engine.blueprints["test_bp"].start_state = "start"
    engine.blueprints["interval_bp"].start_state = "start"
    return engine


@pytest.fixture
def scheduler(engine):
    return Scheduler(engine)


def test_load_schedules_from_file():
    toml_content = b"""
    [cleanup_job]
    blueprint = "system_cleanup"
    interval_seconds = 3600
    input_data = { target = "temp_files" }

    [daily_report]
    blueprint = "generate_report"
    daily_at = "09:00"
    input_data = { type = "full_daily" }
    """
    with tempfile.NamedTemporaryFile(suffix=".toml") as f:
        f.write(toml_content)
        f.flush()

        schedules = load_schedules_from_file(f.name)
        assert len(schedules) == 2

        cleanup = next(j for j in schedules if j.name == "cleanup_job")
        assert cleanup.interval_seconds == 3600
        assert cleanup.blueprint == "system_cleanup"
        assert cleanup.input_data["target"] == "temp_files"

        daily = next(j for j in schedules if j.name == "daily_report")
        assert daily.daily_at == "09:00"


@pytest.mark.asyncio
async def test_scheduler_interval_job(scheduler, engine):
    job = ScheduledJobConfig(name="interval_job", blueprint="interval_bp", input_data={}, interval_seconds=10)
    scheduler.schedules = [job]

    engine.create_background_job = AsyncMock()

    with patch("avtomatika.storage.memory.monotonic") as mock_monotonic:
        start_mono = 1000.0
        mock_monotonic.return_value = start_mono

        now_tz = datetime(2023, 1, 1, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        await scheduler._process_job(job, now_tz)

        engine.create_background_job.assert_called_once()
        assert await scheduler.storage.get_str(f"scheduler:last_run:{job.name}") is not None

        engine.create_background_job.reset_mock()

        mock_monotonic.return_value = start_mono + 5.0
        now_tz_2 = datetime(2023, 1, 1, 12, 0, 5, tzinfo=ZoneInfo("UTC"))
        await scheduler._process_job(job, now_tz_2)
        engine.create_background_job.assert_not_called()

        mock_monotonic.return_value = start_mono + 15.0
        now_tz_3 = datetime(2023, 1, 1, 12, 0, 15, tzinfo=ZoneInfo("UTC"))
        await scheduler._process_job(job, now_tz_3)
        engine.create_background_job.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_daily_job(scheduler, engine):
    job = ScheduledJobConfig(name="daily_job", blueprint="test_bp", input_data={}, daily_at="09:00")
    scheduler.schedules = [job]
    scheduler.timezone = ZoneInfo("UTC")  # Simplified for test

    engine.create_background_job = AsyncMock()

    now_wrong = datetime(2023, 1, 1, 8, 0, 0, tzinfo=ZoneInfo("UTC"))
    await scheduler._process_job(job, now_wrong)
    engine.create_background_job.assert_not_called()

    now_right = datetime(2023, 1, 1, 9, 0, 0, tzinfo=ZoneInfo("UTC"))
    await scheduler._process_job(job, now_right)
    engine.create_background_job.assert_called_once()

    engine.create_background_job.reset_mock()

    await scheduler._process_job(job, now_right)
    engine.create_background_job.assert_not_called()

    now_next_day = datetime(2023, 1, 2, 9, 0, 0, tzinfo=ZoneInfo("UTC"))
    await scheduler._process_job(job, now_next_day)
    engine.create_background_job.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_weekly_job(scheduler, engine):
    job = ScheduledJobConfig(
        name="weekly_job", blueprint="test_bp", input_data={}, weekly_days=["mon", "wed"], time="10:00"
    )
    scheduler.schedules = [job]
    scheduler.timezone = ZoneInfo("UTC")
    engine.create_background_job = AsyncMock()

    now_sun = datetime(2023, 1, 1, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
    await scheduler._process_job(job, now_sun)
    engine.create_background_job.assert_not_called()

    now_mon = datetime(2023, 1, 2, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
    await scheduler._process_job(job, now_mon)
    engine.create_background_job.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_monthly_job(scheduler, engine):
    job = ScheduledJobConfig(
        name="monthly_job", blueprint="test_bp", input_data={}, monthly_dates=[1, 15], time="12:00"
    )
    scheduler.schedules = [job]
    scheduler.timezone = ZoneInfo("UTC")
    engine.create_background_job = AsyncMock()

    now_2nd = datetime(2023, 1, 2, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
    await scheduler._process_job(job, now_2nd)
    engine.create_background_job.assert_not_called()

    now_15th = datetime(2023, 1, 15, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
    await scheduler._process_job(job, now_15th)
    engine.create_background_job.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_run_exits_if_no_schedule(scheduler):
    # Ensure config path is empty
    scheduler.config.SCHEDULES_CONFIG_PATH = ""

    # Using wait_for to ensure it doesn't hang (it should return instantly)
    try:
        await asyncio.wait_for(scheduler.run(), timeout=0.1)
    except TimeoutError:
        pytest.fail("Scheduler.run() did not exit immediately when no schedules were present.")

    assert scheduler._running is False


@pytest.mark.asyncio
async def test_memory_storage_locks():
    storage = MemoryStorage()

    assert await storage.set_nx_ttl("lock1", "val1", 10) is True
    assert await storage.set_nx_ttl("lock1", "val2", 10) is False
    assert await storage.get_str("lock1") == "val1"

    await storage.set_str("key1", "val_str", 10)
    assert await storage.get_str("key1") == "val_str"
