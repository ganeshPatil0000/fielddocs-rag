import io
import json

import pytest

from app.ingestion import handler as ingest_handler
from app.storage import get_store


class FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 - boto3 naming
        if Key not in self.objects:
            raise RuntimeError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Key]), "Metadata": {"uploaded-by": "admin-1"}}


def _sqs_event(*keys: str, message_id: str = "m1") -> dict:
    records = [
        {
            "eventSource": "aws:s3",
            "s3": {"bucket": {"name": "docs"}, "object": {"key": k, "size": 100}},
        }
        for k in keys
    ]
    return {"Records": [{"messageId": message_id, "body": json.dumps({"Records": records})}]}


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3({"raw/abc123/fan+notes.md": b"# Fan\n\nThe fan starts at 35 degrees Celsius."})
    monkeypatch.setattr(ingest_handler, "_s3_client", lambda: fake)
    return fake


def test_parse_key():
    assert ingest_handler.parse_key("raw/abc/manual.pdf", "raw/") == ("abc", "manual.pdf")
    doc_id, name = ingest_handler.parse_key("raw/loose.pdf", "raw/")
    assert len(doc_id) == 16 and name == "loose.pdf"


def test_handler_ingests_and_reports_no_failures(s3):
    # S3 event keys are URL-encoded: '+' is a space.
    s3.objects["raw/abc123/fan notes.md"] = s3.objects.pop("raw/abc123/fan+notes.md")
    result = ingest_handler.handler(_sqs_event("raw/abc123/fan+notes.md"), None)
    assert result == {"batchItemFailures": []}
    doc = get_store().get_document("abc123")
    assert doc["status"] == "INDEXED" and doc["uploaded_by"] == "admin-1"


def test_failed_message_is_returned_for_retry(s3):
    result = ingest_handler.handler(_sqs_event("raw/zzz/missing.md", message_id="m9"), None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "m9"}]}


def test_s3_test_event_is_ignored(s3):
    event = {"Records": [{"messageId": "t", "body": json.dumps({"Event": "s3:TestEvent"})}]}
    assert ingest_handler.handler(event, None) == {"batchItemFailures": []}
