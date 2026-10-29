import { describe, expect, it, vi } from "vitest";

import { config } from "../config";
import { ApiError, createApi, describeError } from "./client";

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });

describe("createApi", () => {
  it("sends the bearer token and JSON body", async () => {
    const fetchMock = vi.fn(async () => json(200, { answer: "ok", sources: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(async () => "token-123");

    await api.chat("What is healthy power?", "abc123");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${config.apiUrl}/v1/chat`);
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer token-123");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({ question: "What is healthy power?", session_id: "abc123" });
  });

  it("sends no Authorization header in local mode", async () => {
    const fetchMock = vi.fn(async () => json(200, []));
    vi.stubGlobal("fetch", fetchMock);
    await createApi(async () => null).listDocuments();
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it("turns a 500 into a readable error with the request id", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(500, { detail: "Internal error", request_id: "req-9" })));
    const error = await createApi(async () => null)
      .chat("question", "s1")
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe(
      "Something went wrong on the server (reference req-9). Try again in a moment.",
    );
  });

  it("signs out on 401", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { message: "Unauthorized" })));
    const onUnauthorized = vi.fn();
    await createApi(async () => "expired", onUnauthorized)
      .listDocuments()
      .catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("uploads through the local endpoint when the API has no S3 bucket", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(501, { detail: "No documents bucket configured" }))
      .mockResolvedValueOnce(json(200, { doc_id: "d1", status: "INDEXED", chunk_count: 3 }));
    vi.stubGlobal("fetch", fetchMock);

    const docId = await createApi(async () => null).uploadDocument(new File(["# Guide"], "guide.md"));

    expect(docId).toBe("d1");
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe(`${config.apiUrl}/v1/documents/upload`);
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("uploads straight to S3 with the presigned fields, file last", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        json(200, { doc_id: "d2", key: "raw/d2/guide.md", url: "https://bucket.s3.amazonaws.com/", fields: { key: "raw/d2/guide.md", policy: "p" }, expires_in: 900 }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const docId = await createApi(async () => "t").uploadDocument(new File(["# Guide"], "guide.md"));

    expect(docId).toBe("d2");
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("https://bucket.s3.amazonaws.com/");
    const keys = Array.from((init.body as FormData).keys());
    expect(keys).toEqual(["key", "policy", "file"]);
  });
});

describe("replaceDocument", () => {
  it("asks for an upload link for the same document, then sends the file to S3", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        json(200, { doc_id: "d7", key: "raw/d7/guide-v2.md", url: "https://bucket.s3.amazonaws.com/", fields: { key: "raw/d7/guide-v2.md" }, expires_in: 900 }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const hash = async () => "ab".repeat(32);
    const docId = await createApi(async () => "t", undefined, hash).replaceDocument(
      "d7",
      new File(["# v2"], "guide-v2.md"),
    );

    expect(docId).toBe("d7");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${config.apiUrl}/v1/documents/d7/replace-url`);
    expect(JSON.parse(String(init.body))).toEqual({
      filename: "guide-v2.md",
      content_type: "application/octet-stream",
      sha256: "ab".repeat(32),
    });
    expect((fetchMock.mock.calls[1] as [string, RequestInit])[0]).toBe("https://bucket.s3.amazonaws.com/");
  });

  it("replaces through the local endpoint when the API has no S3 bucket", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(501, { detail: "No documents bucket configured" }))
      .mockResolvedValueOnce(json(200, { doc_id: "d7", status: "INDEXED", version: 2 }));
    vi.stubGlobal("fetch", fetchMock);

    await createApi(async () => null).replaceDocument("d7", new File(["# v2"], "guide.md"));

    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe(`${config.apiUrl}/v1/documents/d7/replace`);
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("shows the API's reason when a replace is refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(409, { detail: "A new version is already being indexed. Wait a minute." })),
    );
    const error = await createApi(async () => "t")
      .replaceDocument("d7", new File(["x"], "a.md"))
      .catch((e: unknown) => e);
    expect((error as ApiError).message).toBe("A new version is already being indexed. Wait a minute.");
  });
});

describe("upload conflicts", () => {
  it("passes the conflict code and existing document id to the page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(409, { detail: "A document named guide.md already exists.", code: "same_name", doc_id: "d1" }),
      ),
    );
    const error = (await createApi(async () => "t", undefined, async () => undefined)
      .uploadDocument(new File(["x"], "guide.md"))
      .catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(409);
    expect(error.code).toBe("same_name");
    expect(error.docId).toBe("d1");
    expect(error.message).toBe("A document named guide.md already exists.");
  });

  it("leaves the hash out when the browser can't compute it", async () => {
    const fetchMock = vi.fn(async () => json(409, { detail: "x", code: "duplicate", doc_id: "d1" }));
    vi.stubGlobal("fetch", fetchMock);
    await createApi(async () => "t", undefined, async () => undefined)
      .uploadDocument(new File(["x"], "a.md"))
      .catch(() => undefined);
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(String(init.body))).toEqual({ filename: "a.md", content_type: "application/octet-stream" });
  });
});

describe("describeError", () => {
  it("explains what to do", () => {
    expect(describeError(0)).toMatch(/check your connection/i);
    expect(describeError(429)).toMatch(/wait a few seconds/i);
    expect(describeError(429, "Too Many Requests")).toMatch(/wait a few seconds/i);
    expect(describeError(429, "You've asked 10 questions in the last minute. Try again in 20 seconds.")).toBe(
      "You've asked 10 questions in the last minute. Try again in 20 seconds.",
    );
    expect(describeError(415, "Allowed types: .pdf, .docx, .txt, .md")).toBe(
      "Allowed types: .pdf, .docx, .txt, .md",
    );
  });
});
