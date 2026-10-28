/**
 * Minimal Amazon Cognito sign-in over its public JSON API (no SDK).
 *
 * Uses the USER_PASSWORD_AUTH flow that the Terraform app client allows, plus the two
 * challenges an admin-created user can meet: setting a first password and a TOTP code.
 * The ID token is what the API Gateway JWT authorizer checks (its audience is the client id).
 */

export interface Tokens {
  idToken: string;
  refreshToken: string;
  /** Epoch milliseconds when the ID token stops being accepted. */
  expiresAt: number;
}

export type ChallengeKind = "newPassword" | "mfaCode";

export type SignInResult =
  | { kind: "signedIn"; tokens: Tokens }
  | { kind: ChallengeKind; session: string; username: string };

export class AuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthError";
  }
}

interface CognitoReply {
  AuthenticationResult?: {
    IdToken: string;
    RefreshToken?: string;
    ExpiresIn?: number;
  };
  ChallengeName?: string;
  ChallengeParameters?: Record<string, string>;
  Session?: string;
  __type?: string;
  message?: string;
}

export function friendlyAuthError(type?: string, message?: string): string {
  const name = (type ?? "").split("#").pop();
  switch (name) {
    case "NotAuthorizedException":
      if (message && /temporary password/i.test(message)) {
        return "Your temporary password has expired. Ask an admin to send a new one.";
      }
      if (message && /refresh token/i.test(message)) return "Your session has ended. Sign in again.";
      return "Email or password is wrong.";
    case "UserNotFoundException":
      return "Email or password is wrong."; // don't reveal which accounts exist
    case "PasswordResetRequiredException":
      return "Your password must be reset. Ask an admin to reset it.";
    case "InvalidPasswordException":
      return message ?? "That password doesn't meet the password rules.";
    case "CodeMismatchException":
    case "ExpiredCodeException":
      return "That code is wrong or expired. Enter the current code from your authenticator app.";
    case "TooManyRequestsException":
    case "LimitExceededException":
      return "Too many attempts. Wait a minute and try again.";
    default:
      return message ?? "Sign-in failed. Try again.";
  }
}

async function call(target: string, body: object, region: string): Promise<CognitoReply> {
  const response = await fetch(`https://cognito-idp.${region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}`,
    },
    body: JSON.stringify(body),
  }).catch(() => null);
  if (!response) throw new AuthError("Can't reach the sign-in service. Check your connection.");
  const data: CognitoReply = await response.json().catch(() => ({}));
  if (!response.ok) throw new AuthError(friendlyAuthError(data.__type, data.message));
  return data;
}

function toTokens(result: NonNullable<CognitoReply["AuthenticationResult"]>, keepRefresh?: string): Tokens {
  return {
    idToken: result.IdToken,
    refreshToken: result.RefreshToken ?? keepRefresh ?? "",
    expiresAt: Date.now() + (result.ExpiresIn ?? 3600) * 1000,
  };
}

function interpret(data: CognitoReply, username: string): SignInResult {
  if (data.AuthenticationResult) return { kind: "signedIn", tokens: toTokens(data.AuthenticationResult) };
  // With email sign-in the real username is an internal id; Cognito returns it here.
  const user = data.ChallengeParameters?.USER_ID_FOR_SRP ?? username;
  if (data.ChallengeName === "NEW_PASSWORD_REQUIRED" && data.Session) {
    return { kind: "newPassword", session: data.Session, username: user };
  }
  if (data.ChallengeName === "SOFTWARE_TOKEN_MFA" && data.Session) {
    return { kind: "mfaCode", session: data.Session, username: user };
  }
  throw new AuthError("This sign-in step isn't supported here. Ask an admin for help.");
}

export async function signIn(
  email: string,
  password: string,
  clientId: string,
  region: string,
): Promise<SignInResult> {
  const data = await call(
    "InitiateAuth",
    {
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: clientId,
      AuthParameters: { USERNAME: email, PASSWORD: password },
    },
    region,
  );
  return interpret(data, email);
}

export async function answerChallenge(
  challenge: { kind: ChallengeKind; session: string; username: string },
  answer: string,
  clientId: string,
  region: string,
): Promise<SignInResult> {
  const responses =
    challenge.kind === "newPassword"
      ? { USERNAME: challenge.username, NEW_PASSWORD: answer }
      : { USERNAME: challenge.username, SOFTWARE_TOKEN_MFA_CODE: answer };
  const data = await call(
    "RespondToAuthChallenge",
    {
      ChallengeName: challenge.kind === "newPassword" ? "NEW_PASSWORD_REQUIRED" : "SOFTWARE_TOKEN_MFA",
      ClientId: clientId,
      Session: challenge.session,
      ChallengeResponses: responses,
    },
    region,
  );
  return interpret(data, challenge.username);
}

export async function refreshTokens(
  refreshToken: string,
  clientId: string,
  region: string,
): Promise<Tokens> {
  const data = await call(
    "InitiateAuth",
    {
      AuthFlow: "REFRESH_TOKEN_AUTH",
      ClientId: clientId,
      AuthParameters: { REFRESH_TOKEN: refreshToken },
    },
    region,
  );
  if (!data.AuthenticationResult) throw new AuthError("Your session has ended. Sign in again.");
  return toTokens(data.AuthenticationResult, refreshToken);
}

export interface IdClaims {
  email?: string;
  groups: string[];
}

/**
 * Read the ID token's claims for display (name, admin menu). This does NOT verify the
 * token: the API Gateway authorizer and the API check every request themselves.
 */
export function readClaims(idToken: string): IdClaims {
  try {
    const part = idToken.split(".")[1];
    const base64 = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    const data = JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
    const groups = data["cognito:groups"];
    return {
      email: typeof data.email === "string" ? data.email : undefined,
      groups: Array.isArray(groups) ? groups.map(String) : [],
    };
  } catch {
    return { groups: [] };
  }
}
