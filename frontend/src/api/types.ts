// Mirrors app/schemas.py. Keep the two in step when the API changes.

export type MatchKind = "semantic" | "keyword" | "both";

export interface Source {
  index: number;
  doc_id: string;
  source: string;
  page: number | null;
  section: string | null;
  score: number;
  match: MatchKind;
  snippet: string;
  cited: boolean;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  llm_calls: number;
  estimated_cost_usd: number;
}

export interface ChatResponse {
  answer: string;
  sources: Source[];
  grounded: boolean;
  refused: boolean;
  cached: boolean;
  trace_id: string;
  session_id: string | null;
  usage: Usage;
  latency_ms: number;
}

export type DocStatus = "PENDING" | "PROCESSING" | "INDEXED" | "FAILED" | "DUPLICATE";

export interface DocumentOut {
  doc_id: string;
  filename: string | null;
  status: DocStatus | string;
  chunk_count: number;
  page_count: number | null;
  uploaded_by: string | null;
  error: string | null;
  created_at: number | null;
  updated_at: number | null;
  /** Version answers use now; goes up by one with each replace. */
  version: number;
  /** Progress of a replacement file; empty or null when there is none. */
  update_status: "PENDING" | "PROCESSING" | "FAILED" | "" | null;
  update_error: string | null;
  pending_filename: string | null;
  /** For DUPLICATE documents: the document that already has this content. */
  duplicate_of?: string | null;
}

export interface UploadUrlResponse {
  doc_id: string;
  key: string;
  url: string;
  fields: Record<string, string>;
  expires_in: number;
}
