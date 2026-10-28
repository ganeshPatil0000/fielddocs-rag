import { useEffect, useMemo, useState } from "react";

import { createApi } from "./api/client";
import { useSession } from "./auth/session";
import { ChatPage } from "./components/ChatPage";
import { DocumentsPage } from "./components/DocumentsPage";
import { FibreMark } from "./components/FibreRibbon";
import { SignIn } from "./components/SignIn";

type View = "ask" | "documents";

const readView = (): View => (window.location.hash.startsWith("#/documents") ? "documents" : "ask");

function useView(): View {
  const [view, setView] = useState<View>(readView);
  useEffect(() => {
    const onChange = () => setView(readView());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return view;
}

export default function App() {
  const session = useSession();
  const view = useView();
  const api = useMemo(() => createApi(session.getToken, session.signOut), [session.getToken, session.signOut]);

  useEffect(() => {
    document.title = view === "documents" ? "Documents | FieldDocs" : "FieldDocs";
  }, [view]);

  if (session.status === "restoring") return <div className="boot" aria-busy="true" />;
  if (session.status === "signedOut") return <SignIn />;

  return (
    <div className="app">
      <a
        className="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Skip to content
      </a>
      <header className="topbar">
        <a className="brand" href="#/">
          <FibreMark />
          <span>FieldDocs</span>
        </a>
        <nav className="nav" aria-label="Main">
          <a href="#/" aria-current={view === "ask" ? "page" : undefined}>
            Ask
          </a>
          <a href="#/documents" aria-current={view === "documents" ? "page" : undefined}>
            Documents
          </a>
        </nav>
        <div className="account">
          {session.status === "local" ? (
            <span className="account-mode" title="The API runs with AUTH_MODE=none">
              Local mode
            </span>
          ) : (
            <>
              <span className="account-email">{session.email}</span>
              <button type="button" className="btn btn-quiet" onClick={session.signOut}>
                Sign out
              </button>
            </>
          )}
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        {/* Both pages stay mounted so a conversation survives a visit to Documents. */}
        <div hidden={view !== "ask"} className="view">
          <ChatPage api={api} />
        </div>
        <div hidden={view !== "documents"} className="view">
          <DocumentsPage api={api} isAdmin={session.isAdmin} active={view === "documents"} />
        </div>
      </main>
    </div>
  );
}
