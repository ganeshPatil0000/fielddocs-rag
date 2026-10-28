import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { authEnabled, config } from "../config";
import { readClaims, refreshTokens } from "./cognito";
import type { IdClaims, Tokens } from "./cognito";

// The refresh token lives in sessionStorage so a page reload keeps you signed in,
// but closing the tab signs you out. The UI never inserts API text as HTML, which
// is what keeps scripts from reading it.
const STORAGE_KEY = "fielddocs.refresh";

export type AuthStatus = "restoring" | "signedOut" | "signedIn" | "local";

export interface Session {
  status: AuthStatus;
  email?: string;
  isAdmin: boolean;
  getToken: () => Promise<string | null>;
  completeSignIn: (tokens: Tokens) => void;
  signOut: () => void;
}

const SessionContext = createContext<Session | null>(null);

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(authEnabled ? "restoring" : "local");
  const [claims, setClaims] = useState<IdClaims>({ groups: [] });
  const tokens = useRef<Tokens | null>(null);
  const refreshing = useRef<Promise<Tokens | null> | null>(null);

  const signOut = useCallback(() => {
    tokens.current = null;
    storage()?.removeItem(STORAGE_KEY);
    setClaims({ groups: [] });
    setStatus(authEnabled ? "signedOut" : "local");
  }, []);

  const completeSignIn = useCallback((next: Tokens) => {
    tokens.current = next;
    if (next.refreshToken) storage()?.setItem(STORAGE_KEY, next.refreshToken);
    setClaims(readClaims(next.idToken));
    setStatus("signedIn");
  }, []);

  // One refresh at a time, however many requests ask for a token at once.
  const refresh = useCallback(async (): Promise<Tokens | null> => {
    const refreshToken = tokens.current?.refreshToken || storage()?.getItem(STORAGE_KEY);
    if (!refreshToken) return null;
    refreshing.current ??= refreshTokens(refreshToken, config.cognitoClientId, config.awsRegion)
      .then((next) => {
        completeSignIn(next);
        return next;
      })
      .catch(() => {
        signOut();
        return null;
      })
      .finally(() => {
        refreshing.current = null;
      });
    return refreshing.current;
  }, [completeSignIn, signOut]);

  const getToken = useCallback(async () => {
    if (!authEnabled) return null;
    const current = tokens.current;
    if (current && current.expiresAt - 60_000 > Date.now()) return current.idToken;
    return (await refresh())?.idToken ?? null;
  }, [refresh]);

  useEffect(() => {
    if (status !== "restoring") return;
    refresh().then((restored) => {
      if (!restored) setStatus("signedOut");
    });
  }, [refresh, status]);

  const value = useMemo<Session>(
    () => ({
      status,
      email: claims.email,
      isAdmin: !authEnabled || claims.groups.includes("admin"),
      getToken,
      completeSignIn,
      signOut,
    }),
    [status, claims, getToken, completeSignIn, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside <SessionProvider>");
  return session;
}
