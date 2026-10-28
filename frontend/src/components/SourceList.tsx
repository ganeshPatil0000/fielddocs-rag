import type { CSSProperties } from "react";

import type { Source } from "../api/types";
import { documentTitle, matchLabel } from "../lib/format";
import { strandStyle } from "../lib/fibre";

export function sourceElementId(prefix: string, turnId: string, index: number): string {
  return `${prefix}-${turnId}-source-${index}`;
}

function SourceItem({ source, id, active }: { source: Source; id: string; active: boolean }) {
  const title = source.section || documentTitle(source.source);
  const where = [source.section ? documentTitle(source.source) : source.source];
  if (source.page) where.push(`page ${source.page}`);
  return (
    <li>
      <article
        id={id}
        tabIndex={-1}
        className={active ? "source is-active" : "source"}
        style={strandStyle(source.index) as CSSProperties}
        aria-current={active ? "true" : undefined}
      >
        <header className="source-head">
          <span className="tag tag-static" aria-hidden="true">
            {source.index}
          </span>
          <div>
            <h3 className="source-title">{title}</h3>
            <p className="source-where">{where.join(", ")}</p>
          </div>
        </header>
        <p className="source-snippet">{source.snippet}</p>
        <p className="source-match">{matchLabel(source.match)}</p>
      </article>
    </li>
  );
}

interface Props {
  sources: Source[];
  turnId: string;
  idPrefix: string;
  activeIndex: number | null;
}

/** Cited sources first; chunks that were retrieved but not used fold away below. */
export function SourceList({ sources, turnId, idPrefix, activeIndex }: Props) {
  const cited = sources.filter((s) => s.cited);
  const shown = cited.length ? cited : sources;
  const others = cited.length ? sources.filter((s) => !s.cited) : [];
  const item = (s: Source) => (
    <SourceItem
      key={s.index}
      source={s}
      id={sourceElementId(idPrefix, turnId, s.index)}
      active={activeIndex === s.index}
    />
  );
  return (
    <div className="sources">
      <ol className="source-list">{shown.map(item)}</ol>
      {others.length > 0 && (
        <details className="also-searched" open={others.some((s) => s.index === activeIndex)}>
          <summary>
            Also searched, not used in the answer ({others.length})
          </summary>
          <ol className="source-list">{others.map(item)}</ol>
        </details>
      )}
    </div>
  );
}
