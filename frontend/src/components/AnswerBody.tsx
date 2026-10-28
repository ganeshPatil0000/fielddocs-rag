import type { CSSProperties } from "react";

import type { Source } from "../api/types";
import { parseAnswer } from "../lib/answer";
import type { Inline } from "../lib/answer";
import { documentTitle } from "../lib/format";
import { strandStyle } from "../lib/fibre";

interface Props {
  text: string;
  sources: Source[];
  onCite: (index: number) => void;
}

export function AnswerBody({ text, sources, onCite }: Props) {
  const byIndex = new Map(sources.map((s) => [s.index, s]));
  const blocks = parseAnswer(text, new Set(byIndex.keys()));

  const renderInline = (parts: Inline[]) =>
    parts.map((part, i) => {
      if (part.kind === "bold") return <strong key={i}>{part.text}</strong>;
      if (part.kind === "text") return <span key={i}>{part.text}</span>;
      const source = byIndex.get(part.index);
      return (
        <button
          key={i}
          type="button"
          className="tag"
          style={strandStyle(part.index) as CSSProperties}
          aria-label={`Source ${part.index}: ${documentTitle(source?.source)}`}
          onClick={() => onCite(part.index)}
        >
          {part.index}
        </button>
      );
    });

  return (
    <div className="answer">
      {blocks.map((block, i) => {
        if (block.kind === "paragraph") return <p key={i}>{renderInline(block.inlines)}</p>;
        const items = block.items.map((item, j) => <li key={j}>{renderInline(item)}</li>);
        return block.ordered ? (
          <ol key={i} start={block.start}>
            {items}
          </ol>
        ) : (
          <ul key={i}>{items}</ul>
        );
      })}
    </div>
  );
}
