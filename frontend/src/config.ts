// Build-time settings (Vite inlines import.meta.env.VITE_* when it builds).
const env: Partial<ImportMetaEnv> = import.meta.env ?? {};

export const config = {
  apiUrl: (env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, ""),
  cognitoClientId: env.VITE_COGNITO_CLIENT_ID || "",
  awsRegion: env.VITE_AWS_REGION || "us-east-1",
};

/** Local mode: the API runs with AUTH_MODE=none, so there is no sign-in. */
export const authEnabled = config.cognitoClientId !== "";

/** Must match MAX_QUESTION_CHARS on the API. */
export const MAX_QUESTION_CHARS = 2000;
export const MIN_QUESTION_CHARS = 3;
