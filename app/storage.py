"""Persistence for document registry, chat history and the answer cache.

One DynamoDB table (single-table design, on-demand billing) holds everything:

=====================  =====================  ==================================
pk                     sk                     purpose
=====================  =====================  ==================================
DOCS                   DOC#<doc_id>           document registry + ingest status
HASH                   SHA#<sha256>           which document owns this exact content
META                   CORPUS                 corpus version (bumped per ingest)
CHAT#<user>#<session>  TURN#<epoch_ms>        conversation turns (TTL)
CACHE#<sha256>         ANSWER                 cached answers (TTL)
=====================  =====================  ==================================

The HASH items act as a unique constraint on file content: a conditional write
lets only one document claim a hash, even when two uploads race.

``expires_at`` is the table's TTL attribute. DynamoDB deletes expired items
lazily (can take hours), so reads also filter on it.
"""

from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol

from app.config import get_settings


class DocStatus:
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"
    # Same content as another document; not indexed, never used for answers.
    DUPLICATE = "DUPLICATE"


def live_version(doc: dict[str, Any] | None) -> int | None:
    """The document version answers may use, or None while nothing is ready.

    A document being replaced stays INDEXED (its current version keeps serving)
    until the new version is complete; ``version`` then moves forward in one write.
    """
    if not doc or doc.get("status") != DocStatus.INDEXED:
        return None
    return int(doc.get("version") or 1)


def _now() -> int:
    return int(time.time())


def _plain(value: Any) -> Any:
    """Convert DynamoDB Decimals back to int/float recursively."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


class Store(Protocol):
    def get_document(self, doc_id: str) -> dict[str, Any] | None: ...
    def get_documents(self, doc_ids: list[str]) -> dict[str, dict[str, Any]]: ...
    def put_document(self, doc: dict[str, Any]) -> None: ...
    def update_document(self, doc_id: str, **fields: Any) -> None: ...
    def list_documents(self) -> list[dict[str, Any]]: ...
    def delete_document(self, doc_id: str) -> None: ...
    # -- content hashes (one owner per exact file content) ---------------------
    def hash_owner(self, content_hash: str) -> str | None: ...
    def claim_hash(self, content_hash: str, doc_id: str, replace: str | None = None) -> str: ...
    def release_hash(self, content_hash: str, doc_id: str) -> None: ...
    def get_corpus_version(self) -> int: ...
    def bump_corpus_version(self) -> int: ...
    def append_turn(self, user: str, session: str, question: str, answer: str) -> None: ...
    def get_history(self, user: str, session: str, limit: int) -> list[tuple[str, str]]: ...
    def cache_get(self, key: str) -> dict[str, Any] | None: ...
    def cache_put(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None: ...


class DynamoStore:
    def __init__(self, table_name: str, table: Any = None) -> None:
        if table is None:
            import boto3

            from app.providers import BOTO_CONFIG

            table = boto3.resource("dynamodb", config=BOTO_CONFIG).Table(table_name)
        self.table = table
        self.settings = get_settings()

    # -- documents ----------------------------------------------------------
    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": "DOCS", "sk": f"DOC#{doc_id}"}).get("Item")
        return _plain(item) if item else None

    def get_documents(self, doc_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Several registry entries in one round trip (BatchGetItem, 100 keys a call)."""
        client = self.table.meta.client
        wanted = sorted(set(doc_ids))
        found: dict[str, dict[str, Any]] = {}
        for start in range(0, len(wanted), 100):
            request: dict[str, Any] = {
                self.table.name: {
                    "Keys": [{"pk": "DOCS", "sk": f"DOC#{d}"} for d in wanted[start : start + 100]]
                }
            }
            for _ in range(5):  # DynamoDB may return some keys as "unprocessed"
                resp = client.batch_get_item(RequestItems=request)
                for item in resp.get("Responses", {}).get(self.table.name, []):
                    found[str(item["sk"]).removeprefix("DOC#")] = _plain(item)
                request = resp.get("UnprocessedKeys") or {}
                if not request:
                    break
        return found

    def put_document(self, doc: dict[str, Any]) -> None:
        now = _now()
        item = {
            "pk": "DOCS",
            "sk": f"DOC#{doc['doc_id']}",
            "created_at": now,
            "updated_at": now,
            **doc,
        }
        self.table.put_item(Item=item)

    def update_document(self, doc_id: str, **fields: Any) -> None:
        fields["updated_at"] = _now()
        names = {f"#{k}": k for k in fields}
        values = {f":{k}": v for k, v in fields.items()}
        expr = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
        self.table.update_item(
            Key={"pk": "DOCS", "sk": f"DOC#{doc_id}"},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def list_documents(self) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"KeyConditionExpression": Key("pk").eq("DOCS")}
        while True:
            resp = self.table.query(**kwargs)
            items.extend(_plain(i) for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def delete_document(self, doc_id: str) -> None:
        self.table.delete_item(Key={"pk": "DOCS", "sk": f"DOC#{doc_id}"})

    # -- content hashes -----------------------------------------------------
    def hash_owner(self, content_hash: str) -> str | None:
        item = self.table.get_item(Key={"pk": "HASH", "sk": f"SHA#{content_hash}"}).get("Item")
        return str(item["doc_id"]) if item else None

    def claim_hash(self, content_hash: str, doc_id: str, replace: str | None = None) -> str:
        """Make ``doc_id`` the owner unless another document owns the hash; returns the
        owner. ``replace`` takes the hash over from that (stale) owner."""
        condition = "attribute_not_exists(pk) OR doc_id = :me"
        values: dict[str, Any] = {":me": doc_id}
        if replace:
            condition += " OR doc_id = :old"
            values[":old"] = replace
        try:
            self.table.put_item(
                Item={"pk": "HASH", "sk": f"SHA#{content_hash}", "doc_id": doc_id, "at": _now()},
                ConditionExpression=condition,
                ExpressionAttributeValues=values,
            )
            return doc_id
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return self.hash_owner(content_hash) or doc_id

    def release_hash(self, content_hash: str, doc_id: str) -> None:
        try:
            self.table.delete_item(
                Key={"pk": "HASH", "sk": f"SHA#{content_hash}"},
                ConditionExpression="doc_id = :me",
                ExpressionAttributeValues={":me": doc_id},
            )
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # owned by another document (or already gone): leave it

    # -- corpus version (invalidates the answer cache) ----------------------
    def get_corpus_version(self) -> int:
        item = self.table.get_item(Key={"pk": "META", "sk": "CORPUS"}).get("Item")
        return int(item["version"]) if item else 0

    def bump_corpus_version(self) -> int:
        resp = self.table.update_item(
            Key={"pk": "META", "sk": "CORPUS"},
            UpdateExpression="ADD #v :one",
            ExpressionAttributeNames={"#v": "version"},
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        return int(resp["Attributes"]["version"])

    # -- chat history -------------------------------------------------------
    def append_turn(self, user: str, session: str, question: str, answer: str) -> None:
        self.table.put_item(
            Item={
                "pk": f"CHAT#{user}#{session}",
                "sk": f"TURN#{int(time.time() * 1000):013d}",
                "question": question,
                "answer": answer,
                "expires_at": _now() + self.settings.history_ttl_seconds,
            }
        )

    def get_history(self, user: str, session: str, limit: int) -> list[tuple[str, str]]:
        if limit <= 0:
            return []
        from boto3.dynamodb.conditions import Key

        resp = self.table.query(
            KeyConditionExpression=Key("pk").eq(f"CHAT#{user}#{session}"),
            ScanIndexForward=False,
            Limit=limit,
        )
        now = _now()
        turns = [
            (i["question"], i["answer"])
            for i in resp.get("Items", [])
            if int(i.get("expires_at", now + 1)) > now
        ]
        return list(reversed(turns))

    # -- answer cache -------------------------------------------------------
    def cache_get(self, key: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": f"CACHE#{key}", "sk": "ANSWER"}).get("Item")
        if not item or int(item.get("expires_at", 0)) <= _now():
            return None
        return json.loads(item["payload"])

    def cache_put(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        self.table.put_item(
            Item={
                "pk": f"CACHE#{key}",
                "sk": "ANSWER",
                "payload": json.dumps(value),
                "expires_at": _now() + ttl_seconds,
            }
        )


class MemoryStore:
    """Thread-safe in-process store for local runs and tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.docs: dict[str, dict[str, Any]] = {}
        self.hashes: dict[str, str] = {}
        self.version = 0
        self.chats: dict[str, list[tuple[str, str]]] = {}
        self.cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        doc = self.docs.get(doc_id)
        return dict(doc) if doc else None

    def get_documents(self, doc_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {d: dict(self.docs[d]) for d in set(doc_ids) if d in self.docs}

    def put_document(self, doc: dict[str, Any]) -> None:
        now = _now()
        with self._lock:
            self.docs[doc["doc_id"]] = {"created_at": now, "updated_at": now, **doc}

    def update_document(self, doc_id: str, **fields: Any) -> None:
        with self._lock:
            self.docs.setdefault(doc_id, {"doc_id": doc_id}).update(fields, updated_at=_now())

    def list_documents(self) -> list[dict[str, Any]]:
        return [dict(d) for d in self.docs.values()]

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            self.docs.pop(doc_id, None)

    def hash_owner(self, content_hash: str) -> str | None:
        return self.hashes.get(content_hash)

    def claim_hash(self, content_hash: str, doc_id: str, replace: str | None = None) -> str:
        with self._lock:
            owner = self.hashes.get(content_hash)
            if owner in (None, doc_id) or (replace and owner == replace):
                self.hashes[content_hash] = doc_id
                return doc_id
            return owner

    def release_hash(self, content_hash: str, doc_id: str) -> None:
        with self._lock:
            if self.hashes.get(content_hash) == doc_id:
                del self.hashes[content_hash]

    def get_corpus_version(self) -> int:
        return self.version

    def bump_corpus_version(self) -> int:
        with self._lock:
            self.version += 1
            return self.version

    def append_turn(self, user: str, session: str, question: str, answer: str) -> None:
        with self._lock:
            self.chats.setdefault(f"{user}#{session}", []).append((question, answer))

    def get_history(self, user: str, session: str, limit: int) -> list[tuple[str, str]]:
        if limit <= 0:
            return []
        return self.chats.get(f"{user}#{session}", [])[-limit:]

    def cache_get(self, key: str) -> dict[str, Any] | None:
        hit = self.cache.get(key)
        if not hit or hit[0] <= time.time():
            return None
        return hit[1]

    def cache_put(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        with self._lock:
            self.cache[key] = (time.time() + ttl_seconds, value)


@lru_cache
def get_store() -> Store:
    s = get_settings()
    if s.storage_backend == "memory":
        return MemoryStore()
    return DynamoStore(s.dynamodb_table)
