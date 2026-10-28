import { describe, expect, it } from "vitest";

import { documentTitle, formatSeconds, matchLabel, newSessionId, timeAgo } from "./format";

describe("format helpers", () => {
  it("makes readable document titles from file names", () => {
    expect(documentTitle("fttp_ont_install_guide.md")).toBe("FTTP ONT install guide");
    expect(documentTitle("street-cabinet-power-alarm-runbook.pdf")).toBe("Street cabinet power alarm runbook");
    expect(documentTitle(null)).toBe("Untitled document");
  });

  it("describes how a source was found in plain words", () => {
    expect(matchLabel("keyword")).toBe("Matched by exact words");
    expect(matchLabel("both")).toBe("Matched by meaning and exact words");
    expect(matchLabel("something-new")).toBe("Matched");
  });

  it("formats durations", () => {
    expect(formatSeconds(412)).toBe("412 ms");
    expect(formatSeconds(2210)).toBe("2.2 s");
  });

  it("formats relative times", () => {
    const now = Date.UTC(2026, 9, 29, 12, 0, 0);
    expect(timeAgo(now / 1000 - 30, now)).toBe("just now");
    expect(timeAgo(now / 1000 - 120, now)).toBe("2 minutes ago");
    expect(timeAgo(now / 1000 - 2 * 86_400, now)).toBe("2 days ago");
    expect(timeAgo(null, now)).toBe("");
  });

  it("creates session ids the API accepts", () => {
    const id = newSessionId();
    expect(id).toMatch(/^[A-Za-z0-9_-]{1,64}$/);
    expect(newSessionId()).not.toBe(id);
  });
});
