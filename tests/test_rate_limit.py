"""Per-user rate limits on /v1/chat."""

from __future__ import annotations

import boto3
import pytest
from botocore.stub import ANY, Stubber
from fastapi.testclient import TestClient

from app import ratelimit
from app.config import get_settings
from app.storage import DynamoStore, MemoryStore

NOON = 1_800_000_000 - (1_800_000_000 % 86400) + 12 * 3600 + 30  # 12:00:30 UTC


def settings(per_minute: int = 3, per_day: int = 5):
    return get_settings().model_copy(
        update={"rate_limit_per_minute": per_minute, "rate_limit_per_day": per_day}
    )


def test_allows_up_to_the_minute_limit_then_says_when_to_retry():
    store, s = MemoryStore(), settings()
    assert all(ratelimit.check(store, "u1", s, now=NOON).allowed for _ in range(3))
    blocked = ratelimit.check(store, "u1", s, now=NOON)
    assert not blocked.allowed and blocked.retry_after == 30
    assert blocked.message == (
        "You've asked 3 questions in the last minute. Try again in 30 seconds."
    )


def test_each_user_has_their_own_limit():
    store, s = MemoryStore(), settings(per_minute=1)
    assert ratelimit.check(store, "u1", s, now=NOON).allowed
    assert not ratelimit.check(store, "u1", s, now=NOON).allowed
    assert ratelimit.check(store, "u2", s, now=NOON).allowed


def test_a_new_minute_starts_a_new_count():
    store, s = MemoryStore(), settings(per_minute=1, per_day=0)
    assert ratelimit.check(store, "u1", s, now=NOON).allowed
    assert not ratelimit.check(store, "u1", s, now=NOON + 10).allowed
    assert ratelimit.check(store, "u1", s, now=NOON + 60).allowed


def test_daily_limit_resets_at_midnight_utc():
    store, s = MemoryStore(), settings(per_minute=0, per_day=2)
    for i in range(2):
        assert ratelimit.check(store, "u1", s, now=NOON + i * 120).allowed
    blocked = ratelimit.check(store, "u1", s, now=NOON + 600)
    assert not blocked.allowed
    assert blocked.message.startswith("You've reached today's limit of 2 questions.")
    assert "11 h 50 min" in blocked.message
    assert ratelimit.check(store, "u1", s, now=NOON + 12 * 3600).allowed  # next day


def test_minute_blocks_do_not_use_up_the_daily_quota():
    store, s = MemoryStore(), settings(per_minute=1, per_day=2)
    assert ratelimit.check(store, "u1", s, now=NOON).allowed
    for _ in range(5):
        assert not ratelimit.check(store, "u1", s, now=NOON).allowed
    assert ratelimit.check(store, "u1", s, now=NOON + 60).allowed  # day count is only 2


def test_zero_turns_limits_off():
    store, s = MemoryStore(), settings(per_minute=0, per_day=0)
    assert all(ratelimit.check(store, "u1", s, now=NOON).allowed for _ in range(50))
    assert store.counters == {}


def test_counter_failure_fails_open():
    class Broken(MemoryStore):
        def hit(self, user, window, ttl_seconds):
            raise ConnectionError("dynamodb down")

    assert ratelimit.check(Broken(), "u1", settings(), now=NOON).allowed


def test_dynamo_counter_is_one_atomic_update_with_ttl():
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("t")
    stub = Stubber(table.meta.client)
    stub.add_response(
        "update_item",
        {"Attributes": {"count": {"N": "4"}}},
        {
            "TableName": "t",
            "Key": {"pk": "RATE#u1", "sk": "M#29999"},
            "UpdateExpression": "ADD #n :one SET expires_at = if_not_exists(expires_at, :exp)",
            "ExpressionAttributeNames": {"#n": "count"},
            "ExpressionAttributeValues": {":one": 1, ":exp": ANY},
            "ReturnValues": "UPDATED_NEW",
        },
    )
    with stub:
        assert DynamoStore("t", table=table).hit("u1", "M#29999", ttl_seconds=120) == 4
    stub.assert_no_pending_responses()


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    from app.providers import reset_caches

    reset_caches()


def test_chat_returns_429_with_retry_after(limited, seeded):
    from app.api.main import create_app

    client = TestClient(create_app())
    question = {"question": "What does a MAINS FAIL alarm mean?"}
    assert client.post("/v1/chat", json=question).status_code == 200
    assert client.post("/v1/chat", json=question).status_code == 200  # cached, still counted

    resp = client.post("/v1/chat", json=question)

    assert resp.status_code == 429
    assert 1 <= int(resp.headers["retry-after"]) <= 60
    assert resp.json()["detail"].startswith("You've asked 2 questions in the last minute.")
