import { config } from "../config";
import type { ChatResponse, DocumentOut, UploadUrlResponse } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly requestId?: string;

  constructor(status: number, message: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

/** Turn an HTTP failure into a sentence that says what happened and what to do. */
export function describeError(status: number, detail?: string, requestId?: string): string {
  if (status === 0) return "Can't reach the FieldDocs service. Check your connection and try again.";
  if (status === 401) return "Your session has ended. Sign in again.";
  if (status === 403) return "Your account doesn't have permission to do that.";
  if (status === 429) return "Too many requests right now. Wait a few seconds and try again.";
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
}

export function createApi(getToken: TokenProvider, onUnauthorized?: () => void) {
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
      throw new ApiError(response.status, describeError(response.status, detail, requestId), requestId);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  /**
   * Deployed: get a presigned S3 POST from the API and send the file straight to S3
   * (an SQS-triggered Lambda indexes it). Local API without S3 answers 501, so fall
   * back to the local upload endpoint, which indexes the file immediately.
   */
  async function uploadDocument(file: File): Promise<string> {
    const contentType = file.type || "application/octet-stream";
    try {
      const target = await request<UploadUrlResponse>("/v1/documents/upload-url", {
        method: "POST",
        body: JSON.stringify({ filename: file.name, content_type: contentType }),
      });
      const form = new FormData();
      for (const [key, value] of Object.entries(target.fields)) form.append(key, value);
      form.append("file", file); // S3 requires the file to be the last field
      const s3 = await fetch(target.url, { method: "POST", body: form }).catch(() => null);
      if (!s3 || !s3.ok) {
        throw new ApiError(
          s3?.status ?? 0,
          "The file didn't upload. Check that it is under 25 MB and try again.",
        );
      }
      return target.doc_id;
    } catch (error) {
      if (error instanceof ApiError && error.status === 501) {
        const form = new FormData();
        form.append("file", file);
        const doc = await request<DocumentOut>("/v1/documents/upload", {
          method: "POST",
          body: form,
        });
        return doc.doc_id;
      }
      throw error;
    }
  }

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
  };
}

export type Api = ReturnType<typeof createApi>;
