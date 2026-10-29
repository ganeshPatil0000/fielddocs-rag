import { config } from "../config";
import { sha256Hex } from "../lib/hash";
import type { ChatResponse, DocumentOut, UploadUrlResponse } from "./types";

/** Machine-readable reasons the API gives for refusing an upload (HTTP 409). */
export type ConflictCode = "duplicate" | "same_name" | "unchanged";

export class ApiError extends Error {
  readonly status: number;
  readonly requestId?: string;
  /** Set on a 409 from an upload: what the conflict is about. */
  readonly code?: ConflictCode | string;
  /** Set on a 409 from an upload: the existing document involved. */
  readonly docId?: string;

  constructor(
    status: number,
    message: string,
    requestId?: string,
    extra: { code?: string; docId?: string } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
    this.code = extra.code;
    this.docId = extra.docId;
  }
}

/** Turn an HTTP failure into a sentence that says what happened and what to do. */
export function describeError(status: number, detail?: string, requestId?: string): string {
  if (status === 0) return "Can't reach the FieldDocs service. Check your connection and try again.";
  if (status === 401) return "Your session has ended. Sign in again.";
  if (status === 403) return "Your account doesn't have permission to do that.";
  // The API's own 429 says which limit was hit and when it resets; API Gateway's
  // whole-API throttle only says "Too Many Requests".
  if (status === 429) {
    return detail && detail !== "Too Many Requests"
      ? detail
      : "Too many requests right now. Wait a few seconds and try again.";
  }
  if (status >= 500) {
    const ref = requestId ? ` (reference ${requestId})` : "";
    return `Something went wrong on the server${ref}. Try again in a moment.`;
  }
  if (detail) return detail;
  if (status === 422) return "Check what you entered and try again.";
  return `The request failed (HTTP ${status}).`;
}

export type TokenProvider = () => Promise<string | null>;

interface ErrorBody {
  detail?: unknown;
  message?: unknown;
  request_id?: unknown;
  code?: unknown;
  doc_id?: unknown;
}

export function createApi(
  getToken: TokenProvider,
  onUnauthorized?: () => void,
  hashFile: (file: File) => Promise<string | undefined> = sha256Hex,
) {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    const token = await getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (typeof init.body === "string") headers.set("Content-Type", "application/json");

    let response: Response;
    try {
      response = await fetch(`${config.apiUrl}${path}`, { ...init, headers });
    } catch {
      throw new ApiError(0, describeError(0));
    }

    if (!response.ok) {
      const body: ErrorBody = await response.json().catch(() => ({}));
      const requestId =
        (typeof body.request_id === "string" ? body.request_id : null) ??
        response.headers.get("x-request-id") ??
        undefined;
      // FastAPI sends {"detail": "..."}; API Gateway sends {"message": "..."}.
      const detail =
        typeof body.detail === "string"
          ? body.detail
          : typeof body.message === "string"
            ? body.message
            : undefined;
      if (response.status === 401) onUnauthorized?.();
      throw new ApiError(response.status, describeError(response.status, detail, requestId), requestId, {
        code: typeof body.code === "string" ? body.code : undefined,
        docId: typeof body.doc_id === "string" ? body.doc_id : undefined,
      });
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  /** Send a file to S3 with presigned POST fields (the file must be the last field). */
  async function sendToS3(target: UploadUrlResponse, file: File): Promise<void> {
    const form = new FormData();
    for (const [key, value] of Object.entries(target.fields)) form.append(key, value);
    form.append("file", file);
    const s3 = await fetch(target.url, { method: "POST", body: form }).catch(() => null);
    if (!s3 || !s3.ok) {
      throw new ApiError(
        s3?.status ?? 0,
        "The file didn't upload. Check that it is under 25 MB and try again.",
      );
    }
  }

  /**
   * Deployed: ask the API for a presigned S3 POST, then send the file straight to S3
   * (an SQS-triggered Lambda indexes it). A local API without S3 answers 501, so fall
   * back to its local endpoint, which indexes the file immediately.
   */
  async function sendFile(presignPath: string, localPath: string, file: File): Promise<string> {
    const contentType = file.type || "application/octet-stream";
    const sha256 = await hashFile(file);
    try {
      const target = await request<UploadUrlResponse>(presignPath, {
        method: "POST",
        body: JSON.stringify({ filename: file.name, content_type: contentType, sha256 }),
      });
      await sendToS3(target, file);
      return target.doc_id;
    } catch (error) {
      if (error instanceof ApiError && error.status === 501) {
        const form = new FormData();
        form.append("file", file);
        const doc = await request<DocumentOut>(localPath, { method: "POST", body: form });
        return doc.doc_id;
      }
      throw error;
    }
  }

  const uploadDocument = (file: File) =>
    sendFile("/v1/documents/upload-url", "/v1/documents/upload", file);

  /** Upload a new version of a document; the current version answers until it is ready. */
  const replaceDocument = (docId: string, file: File) => {
    const base = `/v1/documents/${encodeURIComponent(docId)}`;
    return sendFile(`${base}/replace-url`, `${base}/replace`, file);
  };

  return {
    chat: (question: string, sessionId: string) =>
      request<ChatResponse>("/v1/chat", {
        method: "POST",
        body: JSON.stringify({ question, session_id: sessionId }),
      }),
    feedback: (traceId: string, score: 0 | 1) =>
      request<{ status: string }>("/v1/feedback", {
        method: "POST",
        body: JSON.stringify({ trace_id: traceId, score }),
      }),
    listDocuments: () => request<DocumentOut[]>("/v1/documents"),
    deleteDocument: (docId: string) =>
      request<void>(`/v1/documents/${encodeURIComponent(docId)}`, { method: "DELETE" }),
    uploadDocument,
    replaceDocument,
  };
}

export type Api = ReturnType<typeof createApi>;
