import { useState } from "react";
import type { FormEvent } from "react";

import { answerChallenge, signIn } from "../auth/cognito";
import type { ChallengeKind, SignInResult } from "../auth/cognito";
import { useSession } from "../auth/session";
import { config } from "../config";
import { FibreMark, FibreRibbon } from "./FibreRibbon";

type Step = { kind: "password" } | { kind: ChallengeKind; session: string; username: string };

export function SignIn() {
  const { completeSignIn } = useSession();
  const [step, setStep] = useState<Step>({ kind: "password" });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [answer, setAnswer] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handle = (result: SignInResult) => {
    if (result.kind === "signedIn") completeSignIn(result.tokens);
    else {
      setStep(result);
      setAnswer("");
      setConfirm("");
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (step.kind === "newPassword" && answer !== confirm) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      const { cognitoClientId: clientId, awsRegion: region } = config;
      handle(
        step.kind === "password"
          ? await signIn(email.trim(), password, clientId, region)
          : await answerChallenge(step, answer.trim(), clientId, region),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const title =
    step.kind === "password"
      ? "Sign in to FieldDocs"
      : step.kind === "newPassword"
        ? "Choose your password"
        : "Enter your code";

  return (
    <main className="signin">
      <form className="signin-card" onSubmit={submit} noValidate>
        <FibreRibbon className="ribbon ribbon-wide" />
        <div className="signin-brand">
          <FibreMark />
          <span>FieldDocs</span>
        </div>
        <h1 className="signin-title">{title}</h1>

        {step.kind === "password" && (
          <>
            <p className="signin-lede">Answers from your field manuals and runbooks, with sources.</p>
            <label className="field">
              <span>Work email</span>
              <input
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
          </>
        )}

        {step.kind === "newPassword" && (
          <>
            <p className="signin-lede">
              This is your first sign-in. Use at least 12 characters with upper- and lower-case
              letters and a number.
            </p>
            <label className="field">
              <span>New password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>Confirm new password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
              />
            </label>
          </>
        )}

        {step.kind === "mfaCode" && (
          <>
            <p className="signin-lede">Enter the 6-digit code from your authenticator app.</p>
            <label className="field">
              <span>Code</span>
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={6}
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                required
              />
            </label>
          </>
        )}

        {error && (
          <p className="notice notice-error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy
            ? "Checking…"
            : step.kind === "password"
              ? "Sign in"
              : step.kind === "newPassword"
                ? "Save password and sign in"
                : "Verify code"}
        </button>
        {step.kind === "password" && (
          <p className="signin-help">No account? Ask your FieldDocs admin to add you.</p>
        )}
      </form>
    </main>
  );
}
