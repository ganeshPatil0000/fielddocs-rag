"""Duplicate protection: same content (SHA-256) and same file name."""

from __future__ import annotations

import hashlib
import time

import boto3
from botocore.stub import ANY, Stubber
from fastapi.testclient import TestClient

from app.ingestion import duplicates
from app.ingestion.pipeline import delete_document_vectors, ingest_bytes
from app.providers import get_vector_index
from app.rag.prompts import ANSWER_SYSTEM
from app.storage import DocStatus, DynamoStore, MemoryStore, get_store

RUNBOOK = b"# Mains failure\n\nA MAINS FAIL alarm means the cabinet runs on battery for 4 hours."
RUNBOOK_V2 = b"# Mains failure\n\nA MAINS FAIL alarm means the cabinet runs on battery for 6 hours."


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _vector_ids() -> set[str]:
    return set(get_vector_index().store.store.keys())


def _client() -> TestClient:
    from app.api.main import create_app

    return TestClient(create_app())


# -- ingestion -------------------------------------------------------------------
def test_same_content_under_another_name_is_not_indexed():
    ingest_bytes(RUNBOOK, "runbook.md", "doc-a")
    ids_before = _vector_ids()

    result = ingest_bytes(RUNBOOK, "runbook-copy.md", "doc-b", s3_key="raw/doc-b/runbook-copy.md")

    assert result.status == DocStatus.DUPLICATE and result.duplicate_of == "doc-a"
    doc = get_store().get_document("doc-b")
    assert doc["status"] == DocStatus.DUPLICATE and doc["duplicate_of"] == "doc-a"
    assert doc["error"] == "Same content as runbook.md, which is already indexed."
    assert _vector_ids() == ids_before  # nothing new to search
    assert get_store().hash_owner(sha(RUNBOOK)) == "doc-a"


def test_sqs_redelivery_of_a_duplicate_stays_a_duplicate():
    ingest_bytes(RUNBOOK, "runbook.md", "doc-a")
    ingest_bytes(RUNBOOK, "copy.md", "doc-b")
    again = ingest_bytes(RUNBOOK, "copy.md", "doc-b")
    assert again.status == DocStatus.DUPLICATE and again.duplicate_of == "doc-a"


def test_replacing_with_another_documents_content_is_rejected():
    ingest_bytes(RUNBOOK, "runbook.md", "doc-a")
    ingest_bytes(RUNBOOK_V2, "other.md", "doc-b", s3_key="raw/doc-b/other.md")

    result = ingest_bytes(RUNBOOK, "runbook.md", "doc-b", s3_key="raw/doc-b/runbook.md")

    assert result.status == DocStatus.DUPLICATE
    doc = get_store().get_document("doc-b")
    assert doc["status"] == DocStatus.INDEXED and doc["version"] == 1  # still answering
    assert doc["update_status"] == DocStatus.FAILED and "runbook.md" in doc["update_error"]
    assert result.obsolete_s3_keys == ["raw/doc-b/runbook.md"]


def test_content_is_free_again_after_delete_or_replace():
    ingest_bytes(RUNBOOK, "runbook.md", "doc-a")
    ingest_bytes(RUNBOOK_V2, "runbook.md", "doc-a")  # replace: old content released
    assert get_store().hash_owner(sha(RUNBOOK)) is None
    assert get_store().hash_owner(sha(RUNBOOK_V2)) == "doc-a"
    assert ingest_bytes(RUNBOOK, "old-runbook.md", "doc-b").status == DocStatus.INDEXED

    delete_document_vectors("doc-a")
    get_store().delete_document("doc-a")
    assert get_store().hash_owner(sha(RUNBOOK_V2)) is None


def test_rejected_file_does_not_keep_the_hash():
    ingest_bytes(b"hi", "tiny.txt", "doc-a")  # too short: no usable chunks
    assert get_store().get_document("doc-a")["status"] == DocStatus.FAILED
    assert get_store().hash_owner(sha(b"hi")) is None


def test_stale_claim_from_a_deleted_document_is_taken_over():
    store = get_store()
    store.claim_hash(sha(RUNBOOK), "ghost")  # owner vanished without releasing
    assert ingest_bytes(RUNBOOK, "runbook.md", "doc-a").status == DocStatus.INDEXED
    assert store.hash_owner(sha(RUNBOOK)) == "doc-a"


def test_documents_indexed_before_hash_claims_are_still_found():
    ingest_bytes(RUNBOOK, "runbook.md", "doc-a")
    get_store().release_hash(sha(RUNBOOK), "doc-a")  # like a document from an older release

    result = ingest_bytes(RUNBOOK, "copy.md", "doc-b")

    assert result.duplicate_of == "doc-a"
    assert get_store().hash_owner(sha(RUNBOOK)) == "doc-a"  # claim handed to the original


def test_in_flight_and_stuck_ingests():
    now = time.time()
    processing = {"status": DocStatus.PROCESSING, "updated_at": now}
    assert duplicates.holds(processing, "h")
    assert not duplicates.holds({**processing, "updated_at": now - 3600}, "h")  # crashed
    retrying = {"status": DocStatus.FAILED, "error": "retrying: throttled", "updated_at": now}
    assert duplicates.holds(retrying, "h")
    assert not duplicates.holds({"status": DocStatus.FAILED, "error": "bad pdf"}, "h")
    assert duplicates.holds({"status": DocStatus.INDEXED, "content_hash": "h"}, "h")
    assert not duplicates.holds({"status": DocStatus.INDEXED, "content_hash": "x"}, "h")


def test_memory_store_claims_have_one_owner():
    store = MemoryStore()
    assert store.claim_hash("h", "a") == "a"
    assert store.claim_hash("h", "b") == "a"
    assert store.claim_hash("h", "b", replace="a") == "b"
    store.release_hash("h", "a")  # not the owner: ignored
    assert store.hash_owner("h") == "b"


def test_dynamo_claim_uses_a_conditional_write():
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("t")
    stub = Stubber(table.meta.client)
    store = DynamoStore("t", table=table)
    stub.add_client_error(
        "put_item",
        service_error_code="ConditionalCheckFailedException",
        expected_params={
            "TableName": "t",
            "Item": {"pk": "HASH", "sk": "SHA#abc", "doc_id": "b", "at": ANY},
            "ConditionExpression": "attribute_not_exists(pk) OR doc_id = :me",
            "ExpressionAttributeValues": {":me": "b"},
        },
    )
    stub.add_response(
        "get_item",
        {"Item": {"pk": {"S": "HASH"}, "sk": {"S": "SHA#abc"}, "doc_id": {"S": "a"}}},
        {"TableName": "t", "Key": {"pk": "HASH", "sk": "SHA#abc"}},
    )
    with stub:
        assert store.claim_hash("abc", "b") == "a"
    stub.assert_no_pending_responses()


# -- API -----------------------------------------------------------------------------
def test_upload_url_refuses_an_exact_duplicate_before_upload():
    ingest_bytes(RUNBOOK, "runbook.md", "doc-a")
    resp = _client().post(
        "/v1/documents/upload-url", json={"filename": "copy.md", "sha256": sha(RUNBOOK)}
    )
    assert resp.status_code == 409
    assert resp.json() == {
        "detail": "This exact file is already indexed as runbook.md.",
        "code": "duplicate",
        "doc_id": "doc-a",
    }


def test_upload_url_offers_replace_for_the_same_name():
    ingest_bytes(RUNBOOK, "runbook.md", "doc-a")
    resp = _client().post(
        "/v1/documents/upload-url", json={"filename": "Runbook.md", "sha256": sha(RUNBOOK_V2)}
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "same_name" and body["doc_id"] == "doc-a"


def test_new_file_with_a_new_name_goes_ahead():
    ingest_bytes(RUNBOOK, "runbook.md", "doc-a")
    resp = _client().post(
        "/v1/documents/upload-url", json={"filename": "fan.md", "sha256": sha(RUNBOOK_V2)}
    )
    assert resp.status_code == 501  # passes the checks; tests have no S3 bucket


def test_bad_hash_format_is_rejected():
    resp = _client().post("/v1/documents/upload-url", json={"filename": "a.md", "sha256": "ABC"})
    assert resp.status_code == 422


def test_replace_url_says_when_the_file_is_unchanged_or_belongs_to_another_document():
    ingest_bytes(RUNBOOK, "runbook.md", "doc-a")
    ingest_bytes(RUNBOOK_V2, "other.md", "doc-b")
    client = _client()

    same = client.post(
        "/v1/documents/doc-a/replace-url", json={"filename": "runbook.md", "sha256": sha(RUNBOOK)}
    )
    assert same.status_code == 409 and same.json()["code"] == "unchanged"

    other = client.post(
        "/v1/documents/doc-a/replace-url", json={"filename": "x.md", "sha256": sha(RUNBOOK_V2)}
    )
    assert other.status_code == 409 and other.json()["code"] == "duplicate"
    assert other.json()["doc_id"] == "doc-b"


def test_local_upload_refuses_duplicates_and_same_names():
    client = _client()
    first = client.post("/v1/documents/upload", files={"file": ("runbook.md", RUNBOOK)})
    assert first.status_code == 200
    dup = client.post("/v1/documents/upload", files={"file": ("copy.md", RUNBOOK)})
    assert dup.status_code == 409 and dup.json()["code"] == "duplicate"
    same = client.post("/v1/documents/upload", files={"file": ("runbook.md", RUNBOOK_V2)})
    assert same.status_code == 409 and same.json()["code"] == "same_name"


def test_prompt_forbids_blending_conflicting_values():
    assert "do not combine, average or turn them into a range" in ANSWER_SYSTEM
