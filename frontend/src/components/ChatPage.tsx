import { useCallback, useEffect, useRef, useState } from "react";

import type { Api } from "../api/client";
import type { ChatResponse } from "../api/types";
import { formatSeconds, newSessionId } from "../lib/format";
import { STARTERS } from "../lib/starters";
import { AnswerBody } from "./AnswerBody";
import { Composer } from "./Composer";
import { FibreRibbon } from "./FibreRibbon";
import { SourceList, sourceElementId } from "./SourceList";

interface Turn {
  id: string;
  question: string;
  state: "pending" | "done" | "error";
  response?: ChatResponse;
  error?: string;
  feedback?: 0 | 1;
  feedbackError?: string;
}

interface Selection {
  turnId: string;
  index: number | null;
}

const prefersReducedMotion = () =>
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function scrollToSource(turnId: string, index: number) {
  // The sources panel shows on wide screens, the inline list on narrow ones.
  const candidates = ["panel", "inline"]
    .map((prefix) => document.getElementById(sourceElementId(prefix, turnId, index)))
    .filter((el): el is HTMLElement => el !== null && el.offsetParent !== null);
  const target = candidates[0];
  if (!target) return;
  target.scrollIntoView?.({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  target.focus({ preventScroll: true });
}

function Welcome({ onAsk }: { onAsk: (question: string) => void }) {
  return (
    <section className="welcome" aria-labelledby="welcome-title">
      <FibreRibbon />
      <h1 id="welcome-title" className="welcome-title">
        What are you working on?
      </h1>
      <p className="welcome-lede">
        Ask about an install, a fault or an alarm. Every answer shows the manual it came from, and
        tells you plainly when the manuals don't cover it.
      </p>
      <div className="starters">
        {STARTERS.map((group) => (
          <div key={group.job} className="starter-group">
            <h2 className="starter-job">{group.job}</h2>
            <ul>
              {group.questions.map((question) => (
                <li key={question}>
                  <button type="button" className="starter" onClick={() => onAsk(question)}>
                    {question}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

interface TurnProps {
  turn: Turn;
  inlineOpen: boolean;
  activeIndex: number | null;
  onCite: (index: number) => void;
  onToggleSources: () => void;
  onRetry: () => void;
  onFeedback: (score: 0 | 1) => void;
}

function TurnView({ turn, inlineOpen, activeIndex, onCite, onToggleSources, onRetry, onFeedback }: TurnProps) {
  const response = turn.response;
  const cited = response?.sources.filter((s) => s.cited).length ?? 0;
  const sourceCount = cited || response?.sources.length || 0;

  return (
    <article className="turn" aria-busy={turn.state === "pending"}>
      <h2 className="question">{turn.question}</h2>

      {turn.state === "pending" && (
        <p className="pending">
          <span className="led" aria-hidden="true" />
          Searching the manuals
        </p>
      )}

      {turn.state === "error" && (
        <div className="notice notice-error" role="alert">
          <p>{turn.error}</p>
          <button type="button" className="btn btn-quiet" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}

      {response?.refused && (
        <div className="refusal">
          <p className="refusal-title">Not in the manuals</p>
          <p>{response.answer}</p>
        </div>
      )}

      {response && !response.refused && (
        <>
          <AnswerBody text={response.answer} sources={response.sources} onCite={onCite} />
          <footer className="turn-meta">
            {response.grounded ? (
              <span className="check check-ok">
                Checked against {cited} {cited === 1 ? "source" : "sources"}
              </span>
            ) : (
              <span className="check check-warn">Couldn't check this answer against the sources</span>
            )}
            <span className="meta">{formatSeconds(response.latency_ms)}</span>
            {response.cached && <span className="meta">Saved answer</span>}
            {sourceCount > 0 && (
              <button
                type="button"
                className="btn-link inline-sources-toggle"
                aria-expanded={inlineOpen}
                onClick={onToggleSources}
              >
                {inlineOpen ? "Hide sources" : `Show sources (${sourceCount})`}
              </button>
            )}
            <span className="feedback" role="group" aria-label="Was this answer helpful?">
              {turn.feedback === undefined ? (
                <>
                  <span className="meta">Helpful?</span>
                  <button type="button" className="btn-chip" onClick={() => onFeedback(1)}>
                    Yes
                  </button>
                  <button type="button" className="btn-chip" onClick={() => onFeedback(0)}>
                    No
                  </button>
                </>
              ) : (
                <span className="meta" role="status">
                  Thanks for the feedback.
                </span>
              )}
            </span>
          </footer>
          {turn.feedbackError && (
            <p className="notice-inline" role="alert">
              {turn.feedbackError}
            </p>
          )}
          {inlineOpen && (
            <div className="inline-sources">
              <SourceList
                sources={response.sources}
                turnId={turn.id}
                idPrefix="inline"
                activeIndex={activeIndex}
              />
            </div>
          )}
        </>
      )}
    </article>
  );
}

export function ChatPage({ api }: { api: Api }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState(newSessionId);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [inlineOpen, setInlineOpen] = useState<ReadonlySet<string>>(new Set());
  const endRef = useRef<HTMLDivElement>(null);
  const pendingScroll = useRef<Selection | null>(null);
  const busy = turns.some((t) => t.state === "pending");

  const update = (id: string, patch: Partial<Turn>) =>
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const run = useCallback(
    async (id: string, question: string) => {
      try {
        const response = await api.chat(question, sessionId);
        update(id, { state: "done", response, error: undefined });
        setSelection({ turnId: id, index: null });
      } catch (error) {
        update(id, {
          state: "error",
          error: error instanceof Error ? error.message : "The question couldn't be answered.",
        });
      }
    },
    [api, sessionId],
  );

  const ask = (question: string) => {
    const id = newSessionId();
    setTurns((prev) => [...prev, { id, question, state: "pending" }]);
    void run(id, question);
  };

  const retry = (turn: Turn) => {
    update(turn.id, { state: "pending", error: undefined });
    void run(turn.id, turn.question);
  };

  const sendFeedback = async (turn: Turn, score: 0 | 1) => {
    if (!turn.response) return;
    update(turn.id, { feedback: score, feedbackError: undefined });
    try {
      await api.feedback(turn.response.trace_id, score);
    } catch (error) {
      update(turn.id, {
        feedback: undefined,
        feedbackError: error instanceof Error ? error.message : "Feedback wasn't sent.",
      });
    }
  };

  const cite = (turnId: string, index: number) => {
    setSelection({ turnId, index });
    setInlineOpen((prev) => new Set(prev).add(turnId));
    pendingScroll.current = { turnId, index };
  };

  const startOver = () => {
    setTurns([]);
    setSelection(null);
    setInlineOpen(new Set());
    setSessionId(newSessionId());
  };

  // Keep the newest question in view (not on the empty screen, which starts at the top).
  useEffect(() => {
    if (turns.length === 0) return;
    endRef.current?.scrollIntoView?.({ block: "end", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [turns.length]);

  // Scroll to a source after React has rendered it.
  useEffect(() => {
    const target = pendingScroll.current;
    if (target?.index != null) {
      pendingScroll.current = null;
      scrollToSource(target.turnId, target.index);
    }
  }, [selection, inlineOpen]);

  const doneTurns = turns.filter((t) => t.state === "done");
  const panelTurn =
    turns.find((t) => t.id === selection?.turnId && t.state === "done") ?? doneTurns[doneTurns.length - 1];

  return (
    <div className="chat">
      <div className="chat-main">
        <div className="log">
          <div className="log-inner">
            {turns.length === 0 ? (
              <Welcome onAsk={ask} />
            ) : (
              <>
                <div className="log-toolbar">
                  <button type="button" className="btn btn-quiet" onClick={startOver} disabled={busy}>
                    New conversation
                  </button>
                </div>
                {turns.map((turn) => (
                  <TurnView
                    key={turn.id}
                    turn={turn}
                    inlineOpen={inlineOpen.has(turn.id)}
                    activeIndex={selection?.turnId === turn.id ? selection.index : null}
                    onCite={(index) => cite(turn.id, index)}
                    onToggleSources={() =>
                      setInlineOpen((prev) => {
                        const next = new Set(prev);
                        if (next.has(turn.id)) next.delete(turn.id);
                        else next.add(turn.id);
                        return next;
                      })
                    }
                    onRetry={() => retry(turn)}
                    onFeedback={(score) => void sendFeedback(turn, score)}
                  />
                ))}
              </>
            )}
            <div ref={endRef} />
          </div>
        </div>
        <Composer busy={busy} onAsk={ask} />
      </div>

      <aside className="panel" aria-labelledby="panel-title">
        <h2 id="panel-title" className="panel-title">
          Sources
        </h2>
        {!panelTurn?.response ? (
          <p className="panel-empty">
            Sources appear here after you ask a question. Select a numbered tag in an answer to see
            the passage it came from.
          </p>
        ) : panelTurn.response.refused ? (
          <p className="panel-empty">
            No sources for “{panelTurn.question}”. The indexed manuals don't cover it.
          </p>
        ) : (
          <>
            <p className="panel-for">For “{panelTurn.question}”</p>
            <SourceList
              sources={panelTurn.response.sources}
              turnId={panelTurn.id}
              idPrefix="panel"
              activeIndex={selection?.turnId === panelTurn.id ? selection.index : null}
            />
          </>
        )}
      </aside>
    </div>
  );
}
