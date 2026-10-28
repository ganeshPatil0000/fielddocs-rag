import { describe, expect, it, vi } from "vitest";

import { AuthError, answerChallenge, friendlyAuthError, readClaims, refreshTokens, signIn } from "./cognito";

function reply(status: number, body: object) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}

function jwt(payload: object): string {
  const encode = (o: object) => btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "none" })}.${encode(payload)}.sig`;
}

describe("signIn", () => {
  it("calls Cognito InitiateAuth and returns tokens", async () => {
    const fetchMock = reply(200, {
      AuthenticationResult: { IdToken: "id", RefreshToken: "refresh", ExpiresIn: 3600 },
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await signIn("eng@example.com", "pw", "client", "us-east-1");

    expect(result.kind).toBe("signedIn");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://cognito-idp.us-east-1.amazonaws.com/");
    expect(new Headers(init.headers).get("X-Amz-Target")).toBe(
      "AWSCognitoIdentityProviderService.InitiateAuth",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: "client",
      AuthParameters: { USERNAME: "eng@example.com", PASSWORD: "pw" },
    });
  });

  it("asks for a new password on first sign-in, using Cognito's internal username", async () => {
    vi.stubGlobal(
      "fetch",
      reply(200, {
        ChallengeName: "NEW_PASSWORD_REQUIRED",
        Session: "sess",
        ChallengeParameters: { USER_ID_FOR_SRP: "uuid-123" },
      }),
    );
    const result = await signIn("eng@example.com", "temp", "client", "us-east-1");
    expect(result).toEqual({ kind: "newPassword", session: "sess", username: "uuid-123" });

    const fetchMock = reply(200, { AuthenticationResult: { IdToken: "id", RefreshToken: "r" } });
    vi.stubGlobal("fetch", fetchMock);
    if (result.kind !== "newPassword") throw new Error("expected a challenge");
    const done = await answerChallenge(result, "New-password-123", "client", "us-east-1");
    expect(done.kind).toBe("signedIn");
    const body = JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body));
    expect(body.ChallengeResponses).toEqual({ USERNAME: "uuid-123", NEW_PASSWORD: "New-password-123" });
  });

  it("turns Cognito errors into plain messages without revealing which accounts exist", async () => {
    vi.stubGlobal("fetch", reply(400, { __type: "UserNotFoundException", message: "User does not exist." }));
    await expect(signIn("x@example.com", "pw", "c", "us-east-1")).rejects.toThrow("Email or password is wrong.");
  });

  it("reports a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("offline"))));
    await expect(signIn("x@example.com", "pw", "c", "us-east-1")).rejects.toBeInstanceOf(AuthError);
  });
});

describe("refreshTokens", () => {
  it("keeps the existing refresh token (Cognito doesn't send a new one)", async () => {
    vi.stubGlobal("fetch", reply(200, { AuthenticationResult: { IdToken: "new-id", ExpiresIn: 3600 } }));
    const tokens = await refreshTokens("old-refresh", "c", "us-east-1");
    expect(tokens.idToken).toBe("new-id");
    expect(tokens.refreshToken).toBe("old-refresh");
  });
});

describe("friendlyAuthError", () => {
  it("explains an expired temporary password", () => {
    expect(
      friendlyAuthError("NotAuthorizedException", "Temporary password has expired and must be reset by an administrator."),
    ).toMatch(/temporary password has expired/i);
  });
});

describe("readClaims", () => {
  it("reads email and groups from an ID token", () => {
    expect(readClaims(jwt({ email: "eng@example.com", "cognito:groups": ["admin"] }))).toEqual({
      email: "eng@example.com",
      groups: ["admin"],
    });
  });

  it("returns no groups for a malformed token", () => {
    expect(readClaims("not-a-jwt")).toEqual({ groups: [] });
  });
});
