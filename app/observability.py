"""Structured logging, CloudWatch metrics (EMF) and LangSmith flushing.

* Logs are single-line JSON so CloudWatch Logs Insights can query fields
  (``request_id``, ``latency_ms``, ``input_tokens`` ...).
* Metrics use the CloudWatch Embedded Metric Format: printing a JSON blob to
  stdout is enough - no PutMetricData calls, no extra latency or IAM.
* LangSmith batches traces on a background thread. Lambda freezes the process
  between invocations, so we flush explicitly at the end of each request.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_var: ContextVar[str] = ContextVar("user", default="-")

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "user": user_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    # Lambda pre-installs a handler; replace it so every line is JSON.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("botocore", "urllib3", "httpx", "httpcore", "langsmith"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def emit_metrics(
    namespace: str,
    metrics: dict[str, float],
    dimensions: dict[str, str] | None = None,
    unit_overrides: dict[str, str] | None = None,
) -> None:
    """Write metrics in CloudWatch Embedded Metric Format (EMF)."""
    dimensions = dimensions or {}
    unit_overrides = unit_overrides or {}
    blob: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [list(dimensions.keys())],
                    "Metrics": [
                        {"Name": name, "Unit": unit_overrides.get(name, "Count")}
                        for name in metrics
                    ],
                }
            ],
        },
        **dimensions,
        **metrics,
    }
    sys.stdout.write(json.dumps(blob) + "\n")
    sys.stdout.flush()


def flush_traces() -> None:
    """Block until queued LangSmith traces are sent (no-op if tracing is off)."""
    try:
        from langsmith import run_trees

        client = getattr(run_trees, "_CLIENT", None)
        if client is not None:
            try:
                client.flush(timeout=5)
            except TypeError:  # older langsmith versions take no timeout
                client.flush()
    except Exception:  # noqa: BLE001 - observability must never break a request
        logging.getLogger(__name__).debug("langsmith flush failed", exc_info=True)
