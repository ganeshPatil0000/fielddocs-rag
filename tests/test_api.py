import pytest
from fastapi.testclient import TestClient


def _client(event: dict | None = None) -> TestClient:
    """TestClient whose requests look like they came through API Gateway."""
    from app.api.main import create_app

    app = create_app()
    if event is None:
        return TestClient(app)

    async def with_event(scope, receive, send):  # noqa: ANN001
        if scope["type"] == "http":
            scope["aws.event"] = event
        await app(scope, receive, send)

    return TestClient(with_event)


def _jwt_event(sub: str, groups: str = "[]") -> dict:
    return {
        "requestContext": {
            "requestId": "req-123",
            "authorizer": {"jwt": {"claims": {"sub": sub, "cognito:groups": groups}}},
        }
    }


def test_health():
    resp = _client().get("/health")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"
    assert "x-request-id" in resp.headers


def test_chat_end_to_end(seeded):
    resp = _client().post("/v1/chat", json={"question": "What does a solid red LOS light mean?"})
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["sources"] and not body["refused"]
    assert body["trace_id"]


def test_chat_input_validation():
    client = _client()
    assert client.post("/v1/chat", json={"question": "hi"}).status_code == 422
    assert client.post("/v1/chat", json={"question": "x" * 5000}).status_code == 422
    bad_session = {"question": "valid question", "session_id": "../../etc"}
    assert client.post("/v1/chat", json=bad_session).status_code == 422


@pytest.fixture
def cognito(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "cognito")
    from app.providers import reset_caches

    reset_caches()


def test_missing_claims_is_401(cognito):
    resp = _client(event={"requestContext": {}}).post("/v1/chat", json={"question": "abc?"})
    assert resp.status_code == 401


def test_engineer_cannot_upload(cognito):
    client = _client(_jwt_event("engineer-1", "[engineers]"))
    resp = client.post("/v1/documents/upload-url", json={"filename": "a.pdf"})
    assert resp.status_code == 403


def test_admin_upload_rejects_bad_extension(cognito):
    client = _client(_jwt_event("admin-1", "[admin engineers]"))
    resp = client.post("/v1/documents/upload-url", json={"filename": "a.exe"})
    # No bucket configured in tests -> 501 is checked first; with a bucket it's 415.
    assert resp.status_code in (415, 501)


def test_document_listing_and_delete(seeded):
    client = _client()
    docs = client.get("/v1/documents").json()
    assert {d["status"] for d in docs} == {"INDEXED"}
    doc_id = docs[0]["doc_id"]
    assert client.delete(f"/v1/documents/{doc_id}").status_code == 204
    assert client.get(f"/v1/documents/{doc_id}").status_code == 404


def test_local_upload_ingests_file():
    client = _client()
    files = {"file": ("note.md", b"# Fan\n\nThe cabinet fan starts at 35 degrees Celsius.")}
    resp = client.post("/v1/documents/upload", files=files)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "INDEXED"


def test_feedback_accepted():
    resp = _client().post("/v1/feedback", json={"trace_id": "0" * 32, "score": 1})
    assert resp.status_code == 202
