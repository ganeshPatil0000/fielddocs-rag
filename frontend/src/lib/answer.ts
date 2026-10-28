/**
 * Turns the model's answer text into blocks the UI renders as React elements.
 *
 * LLM output is never inserted as HTML: we only recognise the few shapes the prompt
 * asks for (short paragraphs, bullet and numbered steps, **bold**) and citation
 * markers like [2]. A citation becomes a link to its source only if that source
 * exists; anything else stays plain text.
 */

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "bold"; text: string }
  | { kind: "cite"; index: number };

export type Block =
  | { kind: "paragraph"; inlines: Inline[] }
  | { kind: "list"; ordered: boolean; start: number; items: Inline[][] };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*(\d{1,3})[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;
const INLINE = /(\*\*[^*\n]+\*\*|\[\d{1,2}\])/g;

export function parseInline(text: string, citable: ReadonlySet<number>): Inline[] {
  const out: Inline[] = [];
  const push = (part: Inline) => {
    const last = out[out.length - 1];
    if (part.kind === "text" && last?.kind === "text") last.text += part.text;
    else out.push(part);
  };
  for (const piece of text.split(INLINE)) {
    if (!piece) continue;
    const cite = /^\[(\d{1,2})\]$/.exec(piece);
    if (cite && citable.has(Number(cite[1]))) push({ kind: "cite", index: Number(cite[1]) });
    else if (piece.startsWith("**") && piece.endsWith("**") && piece.length > 4)
      push({ kind: "bold", text: piece.slice(2, -2) });
    else push({ kind: "text", text: piece });
  }
  return out;
}

export function parseAnswer(text: string, citable: ReadonlySet<number>): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ kind: "paragraph", inlines: parseInline(paragraph.join(" "), citable) });
      paragraph = [];
    }
  };
  const addListItem = (ordered: boolean, number: number, body: string) => {
    flushParagraph();
    const last = blocks[blocks.length - 1];
    const item = parseInline(body, citable);
    if (last?.kind === "list" && last.ordered === ordered) last.items.push(item);
    else blocks.push({ kind: "list", ordered, start: number, items: [item] });
  };

  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    let match: RegExpExecArray | null;
    if (!line.trim()) {
      flushParagraph();
    } else if ((match = NUMBERED.exec(line))) {
      addListItem(true, Number(match[1]), match[2]);
    } else if ((match = BULLET.exec(line))) {
      addListItem(false, 1, match[1]);
    } else if ((match = HEADING.exec(line))) {
      flushParagraph();
      blocks.push({ kind: "paragraph", inlines: [{ kind: "bold", text: match[1] }] });
    } else {
      // A plain line straight after a list item continues that item.
      const last = blocks[blocks.length - 1];
      if (!paragraph.length && last?.kind === "list" && /^\s{2,}/.test(raw)) {
        last.items[last.items.length - 1].push(...parseInline(` ${line.trim()}`, citable));
      } else {
        paragraph.push(line.trim());
      }
    }
  }
  flushParagraph();
  return blocks;
}
