import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";

import type { Api } from "../api/client";
import type { DocumentOut } from "../api/types";
import { documentTitle, timeAgo } from "../lib/format";

const ACCEPT = ".pdf,.docx,.txt,.md";
const IN_PROGRESS = new Set(["PENDING", "PROCESSING"]);

const STATUS_LABEL: Record<string, string> = {
  INDEXED: "Ready",
  PROCESSING: "Indexing",
  PENDING: "Waiting",
  FAILED: "Failed",
};

interface Props {
  api: Api;
  isAdmin: boolean;
  active: boolean;
}

export function DocumentsPage({ api, isAdmin, active }: Props) {
  const [docs, setDocs] = useState<DocumentOut[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const list = await api.listDocuments();
      setDocs([...list].reverse()); // newest first
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Documents couldn't be loaded.");
    }
  }, [api]);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  // While anything is still indexing, check again every few seconds.
  const indexing = docs?.some((d) => IN_PROGRESS.has(d.status)) ?? false;
  useEffect(() => {
    if (!active || !indexing) return;
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, [active, indexing, load]);

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setNotice(null);
    setUploading(file.name);
    try {
      await api.uploadDocument(file);
      setNotice({
        tone: "ok",
        text: `Uploaded ${file.name}. It can be used in answers once its status shows Ready.`,
      });
      await load();
    } catch (error) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : `${file.name} didn't upload.`,
      });
    } finally {
      setUploading(null);
    }
  };

  const remove = async (doc: DocumentOut) => {
    const name = doc.filename ?? doc.doc_id;
    if (!window.confirm(`Delete ${name}? Answers will stop using it.`)) return;
    setNotice(null);
    try {
      await api.deleteDocument(doc.doc_id);
      setDocs((prev) => prev?.filter((d) => d.doc_id !== doc.doc_id) ?? null);
      setNotice({ tone: "ok", text: `Deleted ${name}.` });
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : `${name} wasn't deleted.` });
    }
  };

  return (
    <div className="docs">
      <header className="docs-head">
        <div>
          <h1 className="page-title">Documents</h1>
          <p className="page-lede">The manuals and runbooks FieldDocs answers from.</p>
        </div>
        {isAdmin && (
          <div>
            <input
              ref={fileInput}
              id="upload"
              type="file"
              accept={ACCEPT}
              className="sr-only"
              onChange={(e) => void upload(e)}
              disabled={uploading !== null}
            />
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => fileInput.current?.click()}
              disabled={uploading !== null}
            >
              {uploading ? `Uploading ${uploading}…` : "Upload document"}
            </button>
            <p className="docs-hint">PDF, Word, Markdown or text, up to 25 MB.</p>
          </div>
        )}
      </header>

      {notice && (
        <p className={`notice notice-${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>
          {notice.text}
        </p>
      )}

      {loadError && (
        <div className="notice notice-error" role="alert">
          <p>{loadError}</p>
          <button type="button" className="btn btn-quiet" onClick={() => void load()}>
            Try again
          </button>
        </div>
      )}

      {docs === null && !loadError && <p className="docs-loading">Loading documents…</p>}

      {docs?.length === 0 && (
        <div className="docs-empty">
          <h2>No documents yet</h2>
          <p>
            {isAdmin
              ? "Upload a manual or runbook to start getting answers from it."
              : "Ask a FieldDocs admin to upload the manuals your team uses."}
          </p>
        </div>
      )}

      {docs && docs.length > 0 && (
        <div className="docs-table-wrap">
          <table className="docs-table">
            <thead>
              <tr>
                <th scope="col">Document</th>
                <th scope="col">Status</th>
                <th scope="col" className="num">
                  Pages
                </th>
                <th scope="col" className="num">
                  Passages
                </th>
                <th scope="col">Updated</th>
                {isAdmin && (
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {docs.map((doc) => (
                <tr key={doc.doc_id}>
                  <td>
                    <span className="doc-name">{documentTitle(doc.filename)}</span>
                    <span className="doc-file">{doc.filename ?? doc.doc_id}</span>
                    {doc.status === "FAILED" && doc.error && (
                      <span className="doc-error">{doc.error.replace(/^retrying: /, "Retrying: ")}</span>
                    )}
                  </td>
                  <td>
                    <span className={`status status-${doc.status.toLowerCase()}`}>
                      {STATUS_LABEL[doc.status] ?? doc.status}
                    </span>
                  </td>
                  <td className="num">{doc.page_count ?? "–"}</td>
                  <td className="num">{doc.chunk_count || "–"}</td>
                  <td>{timeAgo(doc.updated_at ?? doc.created_at)}</td>
                  {isAdmin && (
                    <td className="actions">
                      <button type="button" className="btn-link btn-danger" onClick={() => void remove(doc)}>
                        Delete<span className="sr-only"> {doc.filename}</span>
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
