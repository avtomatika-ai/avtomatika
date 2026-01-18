from asyncio import sleep
from dataclasses import asdict, dataclass
from logging import getLogger
from typing import Any

from aiohttp import ClientSession, ClientTimeout

logger = getLogger(__name__)


@dataclass
class WebhookPayload:
    event: str  # "job_finished", "job_failed", "job_quarantined"
    job_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


class WebhookSender:
    def __init__(self, session: ClientSession):
        self.session = session
        self.timeout = ClientTimeout(total=10)
        self.max_retries = 3

    async def send(self, url: str, payload: WebhookPayload) -> bool:
        """
        Sends a webhook payload to the specified URL with retries.
        Returns True if successful, False otherwise.
        """
        data = asdict(payload)
        for attempt in range(1, self.max_retries + 1):
            try:
                async with self.session.post(url, json=data, timeout=self.timeout) as response:
                    if 200 <= response.status < 300:
                        logger.info(f"Webhook sent successfully to {url} for job {payload.job_id}")
                        return True
                    else:
                        logger.warning(
                            f"Webhook failed for job {payload.job_id} to {url}. "
                            f"Status: {response.status}. Attempt {attempt}/{self.max_retries}"
                        )
            except Exception as e:
                logger.warning(
                    f"Error sending webhook for job {payload.job_id} to {url}: {e}. "
                    f"Attempt {attempt}/{self.max_retries}"
                )

            # Exponential backoff
            if attempt < self.max_retries:
                await sleep(2**attempt)

        logger.error(f"Failed to send webhook for job {payload.job_id} to {url} after {self.max_retries} attempts.")
        return False
