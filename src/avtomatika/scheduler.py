from asyncio import CancelledError, sleep
from datetime import datetime
from logging import getLogger
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .scheduler_config_loader import ScheduledJobConfig, load_schedules_from_file

if TYPE_CHECKING:
    from .engine import OrchestratorEngine

logger = getLogger(__name__)


class Scheduler:
    def __init__(self, engine: "OrchestratorEngine"):
        self.engine = engine
        self.config = engine.config
        self.storage = engine.storage
        self._running = False
        self.schedules: list[ScheduledJobConfig] = []
        self.timezone = ZoneInfo(self.config.TZ)

    def load_config(self) -> None:
        if not self.config.SCHEDULES_CONFIG_PATH:
            logger.info("No SCHEDULES_CONFIG_PATH set. Scheduler will not run any jobs.")
            return

        try:
            self.schedules = load_schedules_from_file(self.config.SCHEDULES_CONFIG_PATH)
            logger.info(f"Loaded {len(self.schedules)} scheduled jobs.")
        except Exception as e:
            logger.error(f"Failed to load schedules config: {e}")

    async def run(self) -> None:
        self.load_config()
        if not self.schedules:
            logger.info("No schedules found. Scheduler loop will not start.")
            return

        logger.info("Scheduler started.")
        self._running = True

        while self._running:
            try:
                now_utc = datetime.now(ZoneInfo("UTC"))
                now_tz = now_utc.astimezone(self.timezone)

                for job in self.schedules:
                    await self._process_job(job, now_tz)

                await sleep(1)

            except CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)
                await sleep(30)

        logger.info("Scheduler stopped.")

    def stop(self) -> None:
        self._running = False

    async def _process_job(self, job: ScheduledJobConfig, now_tz: datetime) -> None:
        if job.interval_seconds:
            await self._process_interval_job(job, now_tz)
        else:
            await self._process_calendar_job(job, now_tz)

    async def _process_interval_job(self, job: ScheduledJobConfig, now_tz: datetime) -> None:
        last_run_key = f"scheduler:last_run:{job.name}"
        last_run_ts = await self.storage.get_str(last_run_key)

        now_ts = now_tz.timestamp()

        if last_run_ts and job.interval_seconds is not None and now_ts - float(last_run_ts) < job.interval_seconds:
            return

        lock_key = f"scheduler:lock:interval:{job.name}"
        if await self.storage.set_nx_ttl(lock_key, "locked", ttl=5):
            try:
                await self._trigger_job(job)
                await self.storage.set_str(last_run_key, str(now_ts))
            except Exception as e:
                logger.error(f"Failed to trigger interval job {job.name}: {e}")

    async def _process_calendar_job(self, job: ScheduledJobConfig, now_tz: datetime) -> None:
        target_time_str = job.daily_at or job.time
        if not target_time_str:
            return

        current_time_str = now_tz.strftime("%H:%M")

        if current_time_str != target_time_str:
            return

        if job.weekly_days:
            current_day_str = now_tz.strftime("%a").lower()
            if current_day_str not in [d.lower() for d in job.weekly_days]:
                return

        if job.monthly_dates and now_tz.day not in job.monthly_dates:
            return

        date_str = now_tz.strftime("%Y-%m-%d")
        lock_key = f"scheduler:lock:{job.name}:{date_str}"

        if await self.storage.set_nx_ttl(lock_key, "locked", ttl=86400):
            logger.info(f"Triggering scheduled job {job.name}")
            await self._trigger_job(job)

    async def _trigger_job(self, job: ScheduledJobConfig) -> None:
        try:
            await self.engine.create_background_job(
                blueprint_name=job.blueprint,
                initial_data=job.input_data,
                source=f"scheduler:{job.name}",
                dispatch_timeout=job.dispatch_timeout,
                result_timeout=job.result_timeout,
            )
        except Exception as e:
            logger.error(f"Failed to create background job {job.name}: {e}")
