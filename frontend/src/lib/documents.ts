import type { DocumentOut } from "../api/types";

const IN_PROGRESS = new Set(["PENDING", "PROCESSING"]);
/** An upload link lasts 15 minutes; a replacement still PENDING after that never arrived. */
const UPLOAD_WINDOW_S = 15 * 60;

function replacementActive(doc: DocumentOut, nowS: number): boolean {
  if (doc.update_status === "PROCESSING") return true;
  if (doc.update_status !== "PENDING") return false;
  return nowS - (doc.updated_at ?? 0) < UPLOAD_WINDOW_S;
}

/** True while a document, or a new version of it, is waiting or indexing. */
export function isBusy(doc: DocumentOut, nowS = Date.now() / 1000): boolean {
  return IN_PROGRESS.has(doc.status) || replacementActive(doc, nowS);
}

/** One line about a replacement in progress or a rejected new file, or null. */
export function updateNote(
  doc: DocumentOut,
  nowS = Date.now() / 1000,
): { tone: "busy" | "error"; text: string } | null {
  const current = `version ${doc.version ?? 1}`;
  if (replacementActive(doc, nowS)) {
    const file = doc.pending_filename ? ` (${doc.pending_filename})` : "";
    const retry = doc.update_error?.startsWith("retrying:") ? " Retrying after an error." : "";
    return {
      tone: "busy",
      text: `Indexing a new version${file}. Answers use ${current} until it is ready.${retry}`,
    };
  }
  if (doc.update_status === "FAILED") {
    const error = doc.update_error?.trim();
    const reason = error ? `: ${error}${/[.!?]$/.test(error) ? "" : "."}` : ".";
    return { tone: "error", text: `New version rejected${reason} Answers still use ${current}.` };
  }
  return null;
}
