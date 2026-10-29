import { describe, expect, it } from "vitest";

import type { DocumentOut } from "../api/types";
import { isBusy, updateNote } from "./documents";

const NOW = 1_800_000_000;

function doc(fields: Partial<DocumentOut> = {}): DocumentOut {
  return {
    doc_id: "d1",
    filename: "runbook.md",
    status: "INDEXED",
    chunk_count: 5,
    page_count: 1,
    uploaded_by: "admin",
    error: null,
    created_at: NOW - 3600,
    updated_at: NOW - 10,
    version: 2,
    update_status: "",
    update_error: null,
    pending_filename: null,
    ...fields,
  };
}

describe("isBusy", () => {
  it("is busy while the first version or a new version is indexing", () => {
    expect(isBusy(doc({ status: "PROCESSING" }), NOW)).toBe(true);
    expect(isBusy(doc({ update_status: "PROCESSING" }), NOW)).toBe(true);
    expect(isBusy(doc({ update_status: "PENDING" }), NOW)).toBe(true);
    expect(isBusy(doc(), NOW)).toBe(false);
    expect(isBusy(doc({ update_status: "FAILED" }), NOW)).toBe(false);
  });

  it("stops waiting for a replacement file that never arrived", () => {
    expect(isBusy(doc({ update_status: "PENDING", updated_at: NOW - 16 * 60 }), NOW)).toBe(false);
  });
});

describe("updateNote", () => {
  it("says the current version keeps answering while the new one indexes", () => {
    const note = updateNote(doc({ update_status: "PROCESSING", pending_filename: "runbook-v3.md" }), NOW);
    expect(note).toEqual({
      tone: "busy",
      text: "Indexing a new version (runbook-v3.md). Answers use version 2 until it is ready.",
    });
  });

  it("mentions retries", () => {
    const note = updateNote(doc({ update_status: "PROCESSING", update_error: "retrying: throttled" }), NOW);
    expect(note?.text).toMatch(/Retrying after an error\.$/);
  });

  it("explains a rejected file and which version is still used", () => {
    const note = updateNote(doc({ update_status: "FAILED", update_error: "No usable text chunks in scan.pdf" }), NOW);
    expect(note).toEqual({
      tone: "error",
      text: "New version rejected: No usable text chunks in scan.pdf. Answers still use version 2.",
    });
  });

  it("is quiet when there is no replacement", () => {
    expect(updateNote(doc(), NOW)).toBeNull();
    expect(updateNote(doc({ update_status: null, version: 1 }), NOW)).toBeNull();
  });
});
