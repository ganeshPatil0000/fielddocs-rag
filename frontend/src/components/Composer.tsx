import { useLayoutEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";

import { MAX_QUESTION_CHARS, MIN_QUESTION_CHARS } from "../config";

interface Props {
  busy: boolean;
  onAsk: (question: string) => void;
}

export function Composer({ busy, onAsk }: Props) {
  const [text, setText] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  const question = text.trim();
  const tooLong = question.length > MAX_QUESTION_CHARS;
  const canSend = !busy && question.length >= MIN_QUESTION_CHARS && !tooLong;
  const counting = question.length > MAX_QUESTION_CHARS - 200;

  // Grow with the text, up to about eight lines.
  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSend) return;
    onAsk(question);
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={submit}>
        <label htmlFor="question" className="sr-only">
          Your question
        </label>
        <textarea
          id="question"
          ref={field}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about a procedure, alarm or fault code"
          aria-describedby="question-hint"
          aria-invalid={tooLong || undefined}
        />
        <button type="submit" className="btn btn-primary" disabled={!canSend}>
          Ask
        </button>
      </form>
      <p
        id="question-hint"
        className={`composer-hint${counting ? " is-counting" : ""}${tooLong ? " is-error" : ""}`}
      >
        {counting
          ? `${question.length.toLocaleString()} of ${MAX_QUESTION_CHARS.toLocaleString()} characters`
          : "Enter to ask, Shift + Enter for a new line. Answers come only from your indexed manuals."}
      </p>
    </div>
  );
}
