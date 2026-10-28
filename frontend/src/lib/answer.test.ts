import { describe, expect, it } from "vitest";

import { parseAnswer, parseInline } from "./answer";

const ALL = new Set([1, 2, 3]);

describe("parseInline", () => {
  it("turns known citation markers into cite parts and keeps the rest as text", () => {
    expect(parseInline("Healthy power is -8 to -27 dBm [1][2].", ALL)).toEqual([
      { kind: "text", text: "Healthy power is -8 to -27 dBm " },
      { kind: "cite", index: 1 },
      { kind: "cite", index: 2 },
      { kind: "text", text: "." },
    ]);
  });

  it("leaves a citation to a source that doesn't exist as plain text", () => {
    expect(parseInline("See [7].", ALL)).toEqual([{ kind: "text", text: "See [7]." }]);
  });

  it("supports bold", () => {
    expect(parseInline("Raise **LOS-NET** now", ALL)).toEqual([
      { kind: "text", text: "Raise " },
      { kind: "bold", text: "LOS-NET" },
      { kind: "text", text: " now" },
    ]);
  });

  it("never produces HTML: markup stays as text", () => {
    const parts = parseInline('<img src=x onerror="alert(1)"> [1]', ALL);
    expect(parts[0]).toEqual({ kind: "text", text: '<img src=x onerror="alert(1)"> ' });
  });
});

describe("parseAnswer", () => {
  it("groups paragraphs, bullet lists and numbered steps", () => {
    const text = [
      "A MAINS FAIL alarm means the cabinet is on battery [1].",
      "",
      "1. Check the isolator [1].",
      "2. Check the fuse.",
      "",
      "- Two people are required [2].",
      "- Wear insulated gloves.",
    ].join("\n");
    const blocks = parseAnswer(text, ALL);
    expect(blocks.map((b) => b.kind)).toEqual(["paragraph", "list", "list"]);
    expect(blocks[1]).toMatchObject({ kind: "list", ordered: true, start: 1 });
    expect(blocks[2]).toMatchObject({ kind: "list", ordered: false });
    if (blocks[1].kind === "list") expect(blocks[1].items).toHaveLength(2);
  });

  it("keeps the starting number of a numbered list", () => {
    const [block] = parseAnswer("3. Measure received power.\n4. Test the drop cable.", ALL);
    expect(block).toMatchObject({ kind: "list", ordered: true, start: 3 });
  });

  it("joins wrapped lines into one paragraph", () => {
    const blocks = parseAnswer("Line one\nline two [3]", ALL);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toEqual({
      kind: "paragraph",
      inlines: [
        { kind: "text", text: "Line one line two " },
        { kind: "cite", index: 3 },
      ],
    });
  });

  it("renders markdown headings as bold paragraphs", () => {
    expect(parseAnswer("## Steps", ALL)).toEqual([
      { kind: "paragraph", inlines: [{ kind: "bold", text: "Steps" }] },
    ]);
  });
});
