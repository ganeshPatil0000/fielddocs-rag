import type { MatchKind } from "../api/types";

const ACRONYMS = new Set(["fttp", "fttc", "ont", "olt", "dsl", "pon", "los", "sla", "rca", "faq"]);

/** "fttp_ont_install_guide.md" -> "FTTP ONT install guide". */
export function documentTitle(filename: string | null | undefined): string {
  if (!filename) return "Untitled document";
  const base = filename.replace(/\.[a-z0-9]+$/i, "").replace(/[_-]+/g, " ").trim();
  if (!base) return filename;
  return base
    .split(/\s+/)
    .map((word, i) => {
      if (ACRONYMS.has(word.toLowerCase())) return word.toUpperCase();
      return i === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word;
    })
    .join(" ");
}

export const MATCH_LABEL: Record<MatchKind, string> = {
  semantic: "Matched by meaning",
  keyword: "Matched by exact words",
  both: "Matched by meaning and exact words",
};

export function matchLabel(match: string): string {
  return MATCH_LABEL[match as MatchKind] ?? "Matched";
}

export function formatSeconds(ms: number): string {
  return ms < 1000 ? `${Math.max(1, Math.round(ms))} ms` : `${(ms / 1000).toFixed(1)} s`;
}

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/** Epoch seconds -> "2 minutes ago". */
export function timeAgo(epochSeconds: number | null | undefined, now = Date.now()): string {
  if (!epochSeconds) return "";
  const seconds = Math.round(epochSeconds - now / 1000);
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  for (const [unit, size] of steps) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export function newSessionId(): string {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
