"""Lambda entrypoint: SQS (carrying S3 ObjectCreated events) -> ingestion.

Uses SQS *partial batch responses*: only the failed messages go back to the
queue, so one bad file cannot force re-processing of the whole batch. After
``maxReceiveCount`` attempts a message moves to the dead-letter queue, which
has a CloudWatch alarm.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from urllib.parse import unquote_plus

from app.ssm import load_ssm_secrets

load_ssm_secrets()  # before settings/providers read the environment

from app.config import get_settings  # noqa: E402
from app.ingestion.pipeline import ingest_bytes  # noqa: E402
from app.observability import (  # noqa: E402
    configure_logging,
    emit_metrics,
    flush_traces,
    request_id_var,
)

configure_logging(get_settings().log_level)
logger = logging.getLogger(__name__)

_s3 = None


def _s3_client() -> Any:
    global _s3
    if _s3 is None:
        import boto3

        from app.providers import BOTO_CONFIG

        _s3 = boto3.client("s3", config=BOTO_CONFIG)
    return _s3


def parse_key(key: str, raw_prefix: str) -> tuple[str, str]:
    """Return (doc_id, filename) for ``raw/<doc_id>/<filename>`` keys.

    Files dropped straight into the bucket (not via the API) get a stable id
    derived from their key, so re-uploading the same path updates in place.
    """
    rest = key[len(raw_prefix) :] if key.startswith(raw_prefix) else key
    parts = rest.split("/", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:16], rest


def _process_s3_record(record: dict[str, Any]) -> None:
    settings = get_settings()
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])
    size = int(record["s3"]["object"].get("size", 0))
    doc_id, filename = parse_key(key, settings.raw_prefix)

    if size > settings.max_upload_mb * 1024 * 1024:
        from app.storage import DocStatus, get_store

        logger.warning("object too large, skipping", extra={"key": key, "size": size})
        get_store().update_document(
            doc_id, status=DocStatus.FAILED, error=f"file exceeds {settings.max_upload_mb} MB"
        )
        return

    obj = _s3_client().get_object(Bucket=bucket, Key=key)
    data = obj["Body"].read()
    uploaded_by = obj.get("Metadata", {}).get("uploaded-by")
    result = ingest_bytes(data, filename, doc_id, s3_key=key, uploaded_by=uploaded_by)
    for stale_key in result.obsolete_s3_keys:
        # Old file after a replace, or a rejected new file. Best effort: a leftover
        # object costs a little storage but is never used for answers.
        try:
            _s3_client().delete_object(Bucket=bucket, Key=stale_key)
        except Exception:  # noqa: BLE001
            logger.warning("old file cleanup failed", extra={"key": stale_key}, exc_info=True)
    emit_metrics(
        settings.metrics_namespace,
        {
            "DocumentsIngested": 1 if result.status == "INDEXED" and not result.skipped else 0,
            "DocumentsReplaced": 1 if result.replaced else 0,
            "DocumentsDuplicate": 1 if result.duplicate_of else 0,
            "DocumentsFailed": 1 if result.status == "FAILED" else 0,
            "ChunksIndexed": result.chunks if not result.skipped else 0,
        },
        {"Service": "ingestion", "Environment": settings.app_env},
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for message in event.get("Records", []):
        message_id = message.get("messageId", "unknown")
        request_id_var.set(message_id)
        try:
            body = json.loads(message["body"])
            if body.get("Event") == "s3:TestEvent":  # sent once when notifications are set up
                continue
            for record in body.get("Records", []):
                if record.get("eventSource") == "aws:s3":
                    _process_s3_record(record)
        except Exception:  # noqa: BLE001 - report this message for retry, keep the batch going
            logger.exception("message failed", extra={"message_id": message_id})
            failures.append({"itemIdentifier": message_id})
    flush_traces()
    return {"batchItemFailures": failures}
