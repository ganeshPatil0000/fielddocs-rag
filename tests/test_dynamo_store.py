"""DynamoDB store tests using botocore's built-in Stubber (no network, no moto).

Note: with the boto3 *resource* API the Stubber sees request parameters before
boto3 converts them to DynamoDB's typed format, so expected params are plain
Python values. Stubbed *responses* stay in the typed wire format ({"S": ...}),
because boto3 converts those after the stub returns them.
"""

import time

import boto3
from botocore.stub import Stubber

from app.storage import DynamoStore


def _store():
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("t")
    stub = Stubber(table.meta.client)
    return DynamoStore("t", table=table), stub


def test_bump_corpus_version_uses_atomic_add():
    store, stub = _store()
    stub.add_response(
        "update_item",
        {"Attributes": {"version": {"N": "4"}}},
        {
            "TableName": "t",
            "Key": {"pk": "META", "sk": "CORPUS"},
            "UpdateExpression": "ADD #v :one",
            "ExpressionAttributeNames": {"#v": "version"},
            "ExpressionAttributeValues": {":one": 1},
            "ReturnValues": "UPDATED_NEW",
        },
    )
    with stub:
        assert store.bump_corpus_version() == 4
    stub.assert_no_pending_responses()


def test_expired_cache_entry_is_ignored_even_before_ttl_deletion():
    store, stub = _store()
    expired = str(int(time.time()) - 10)
    stub.add_response(
        "get_item",
        {
            "Item": {
                "pk": {"S": "CACHE#k"},
                "sk": {"S": "ANSWER"},
                "payload": {"S": "{}"},
                "expires_at": {"N": expired},
            }
        },
        {"TableName": "t", "Key": {"pk": "CACHE#k", "sk": "ANSWER"}},
    )
    with stub:
        assert store.cache_get("k") is None


def test_get_document_converts_decimals():
    store, stub = _store()
    stub.add_response(
        "get_item",
        {
            "Item": {
                "pk": {"S": "DOCS"},
                "sk": {"S": "DOC#d1"},
                "doc_id": {"S": "d1"},
                "status": {"S": "INDEXED"},
                "chunk_count": {"N": "12"},
            }
        },
        {"TableName": "t", "Key": {"pk": "DOCS", "sk": "DOC#d1"}},
    )
    with stub:
        doc = store.get_document("d1")
    assert doc["chunk_count"] == 12 and isinstance(doc["chunk_count"], int)