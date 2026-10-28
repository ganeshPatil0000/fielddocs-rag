import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { Api } from "../api/client";
import type { ChatResponse } from "../api/types";
import { ChatPage } from "./ChatPage";

afterEach(cleanup);

const answer: ChatResponse = {
  answer: "Healthy power is -8 to -27 dBm [1].\n\n- Clean the connector [2].",
  sources: [
    { index: 1, doc_id: "d1", source: "fttp_ont_install_guide.md", page: null, section: "Fault code LOS", score: 0.03, match: "both", snippet: "Healthy received power at the ONT is between -8 dBm and -27 dBm.", cited: true },
    { index: 2, doc_id: "d1", source: "fttp_ont_install_guide.md", page: 2, section: null, score: 0.02, match: "keyword", snippet: "Clean the connector end face.", cited: true },
    { index: 3, doc_id: "d1", source: "fttp_ont_install_guide.md", page: null, section: "ONT status lights", score: 0.01, match: "semantic", snippet: "LOS flashing red.", cited: false },
  ],
  grounded: true,
  refused: false,
  cached: false,
  trace_id: "trace-123456",
  session_id: "s",
  usage: { input_tokens: 10, output_tokens: 5, llm_calls: 1, estimated_cost_usd: 0 },
  latency_ms: 1200,
};

function fakeApi(chat: Api["chat"]): Api {
  return {
    chat,
    feedback: vi.fn(async () => ({ status: "accepted" })),
    listDocuments: vi.fn(async () => []),
    deleteDocument: vi.fn(async () => undefined),
    uploadDocument: vi.fn(async () => "d1"),
  };
}

function ask(question: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
}

describe("ChatPage", () => {
  it("answers a starter question with fibre-coloured citations and sources", async () => {
    const chat = vi.fn(async () => answer);
    render(<ChatPage api={fakeApi(chat)} />);

    fireEvent.click(screen.getByRole("button", { name: "What received power is healthy at the ONT?" }));

    expect(await screen.findByText("Checked against 2 sources")).toBeTruthy();
    expect(chat).toHaveBeenCalledWith("What received power is healthy at the ONT?", expect.any(String));
    const tag = screen.getByRole("button", { name: "Source 1: FTTP ONT install guide" });
    expect(tag.style.getPropertyValue("--strand").toLowerCase()).toBe("#1f5fbf");
    expect(screen.getAllByText("Matched by meaning and exact words").length).toBeGreaterThan(0);
    expect(screen.getByText(/Also searched, not used in the answer \(1\)/)).toBeTruthy();
  });

  it("highlights the source when its citation is selected", async () => {
    render(<ChatPage api={fakeApi(vi.fn(async () => answer))} />);
    ask("What is healthy power?");
    fireEvent.click(await screen.findByRole("button", { name: /Source 2:/ }));
    const active = document.querySelectorAll('[aria-current="true"]');
    expect(active.length).toBeGreaterThan(0);
    expect(active[0].textContent).toContain("Clean the connector end face.");
  });

  it("shows a refusal plainly", async () => {
    const refused = { ...answer, answer: "I couldn't find this.", sources: [], refused: true };
    render(<ChatPage api={fakeApi(vi.fn(async () => refused))} />);
    ask("How do I configure BGP?");
    expect(await screen.findByText("Not in the manuals")).toBeTruthy();
    expect(screen.getByText(/No sources for/)).toBeTruthy();
  });

  it("shows an error with a retry", async () => {
    const chat = vi
      .fn<Api["chat"]>()
      .mockRejectedValueOnce(new ApiError(503, "Something went wrong on the server. Try again in a moment."))
      .mockResolvedValueOnce(answer);
    render(<ChatPage api={fakeApi(chat)} />);
    ask("What is healthy power?");

    expect((await screen.findByRole("alert")).textContent).toContain("Something went wrong on the server");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Checked against 2 sources")).toBeTruthy();
    expect(chat).toHaveBeenCalledTimes(2);
  });

  it("sends feedback for an answer", async () => {
    const api = fakeApi(vi.fn(async () => answer));
    render(<ChatPage api={api} />);
    ask("What is healthy power?");
    fireEvent.click(await screen.findByRole("button", { name: "Yes" }));
    expect(api.feedback).toHaveBeenCalledWith("trace-123456", 1);
    expect(await screen.findByText("Thanks for the feedback.")).toBeTruthy();
  });

  it("won't send a question that is too short", () => {
    render(<ChatPage api={fakeApi(vi.fn(async () => answer))} />);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "hi" } });
    expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
