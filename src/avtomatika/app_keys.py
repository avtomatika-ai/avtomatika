from asyncio import Task
from typing import TYPE_CHECKING

from aiohttp import ClientSession
from aiohttp.web import AppKey

if TYPE_CHECKING:
    pass

# Application keys for storing components
# Using strings for types where possible to avoid circular imports during runtime,
# or specific imports where safe.

# Main Engine
ENGINE_KEY = AppKey("engine", "OrchestratorEngine")
HTTP_SESSION_KEY = AppKey("http_session", ClientSession)

# Core Components
DISPATCHER_KEY = AppKey("dispatcher", "Dispatcher")
EXECUTOR_KEY = AppKey("executor", "JobExecutor")
WATCHER_KEY = AppKey("watcher", "Watcher")
REPUTATION_CALCULATOR_KEY = AppKey("reputation_calculator", "ReputationCalculator")
HEALTH_CHECKER_KEY = AppKey("health_checker", "HealthChecker")
SCHEDULER_KEY = AppKey("scheduler", "Scheduler")
WS_MANAGER_KEY = AppKey("ws_manager", "WebSocketManager")

# Background Tasks
EXECUTOR_TASK_KEY = AppKey("executor_task", Task)
WATCHER_TASK_KEY = AppKey("watcher_task", Task)
REPUTATION_CALCULATOR_TASK_KEY = AppKey("reputation_calculator_task", Task)
HEALTH_CHECKER_TASK_KEY = AppKey("health_checker_task", Task)
SCHEDULER_TASK_KEY = AppKey("scheduler_task", Task)
S3_SERVICE_KEY = AppKey("s3_service", "S3Service")
