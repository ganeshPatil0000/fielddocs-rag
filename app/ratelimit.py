"""Per-user rate limits for questions (fixed windows in DynamoDB).

Two layers protect the API:
* API Gateway throttles the whole API (``api_throttle_rate`` requests/second,
  shared by everyone) before Lambda even runs;
* this module limits each signed-in user: ``RATE_LIMIT_PER_MINUTE`` stops one
  person (or a stolen token, or a loop in a script) from using all of that
  capacity, and ``RATE_LIMIT_PER_DAY`` caps what one account can spend on LLM calls.

A fixed window is a counter per user per minute (and per UTC day): one atomic
DynamoDB ``ADD`` per request, items expire by TTL. It allows a short burst at a
window edge (up to 2x the limit across two minutes), which is fine for cost
control and much simpler than a sliding window or token bucket.

If DynamoDB itself fails, the request is allowed (fail open): an outage of the
counter must not take answers down with it. The ``RateLimitErrors`` metric shows it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.config import Settings
from app.observability import emit_metrics
from app.storage import Store

logger = logging.getLogger(__name__)

DAY_S = 24 * 60 * 60


@dataclass
class Decision:
    allowed: bool
    retry_after: int = 0  # seconds until the window resets (when not allowed)
    message: str = ""


def _wait(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} seconds"
    hours, minutes = divmod(round(seconds / 60), 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} minutes"


def check(store: Store, user: str, settings: Settings, now: float | None = None) -> Decision:
    """Count this question for ``user`` and say whether it may go ahead."""
    now = time.time() if now is None else now
    try:
        if settings.rate_limit_per_minute:
            minute = int(now // 60)
            count = store.hit(user, f"M#{minute}", ttl_seconds=120)
            if count > settings.rate_limit_per_minute:
                wait = max(1, 60 - int(now % 60))
                return Decision(
                    False,
                    wait,
                    f"You've asked {settings.rate_limit_per_minute} questions in the last minute. "
                    f"Try again in {_wait(wait)}.",
                )
        if settings.rate_limit_per_day:
            day = int(now // DAY_S)
            count = store.hit(user, f"D#{day}", ttl_seconds=2 * DAY_S)
            if count > settings.rate_limit_per_day:
                wait = max(1, DAY_S - int(now % DAY_S))
                return Decision(
                    False,
                    wait,
                    f"You've reached today's limit of {settings.rate_limit_per_day} questions. "
                    f"It resets in {_wait(wait)} (midnight UTC).",
                )
    except Exception:  # noqa: BLE001 - never block answers because the counter failed
        logger.warning("rate limit check failed; allowing the request", exc_info=True)
        emit_metrics(
            settings.metrics_namespace,
            {"RateLimitErrors": 1},
            {"Service": "api", "Environment": settings.app_env},
        )
    return Decision(True)
