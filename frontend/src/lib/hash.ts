/**
 * SHA-256 of a file, as lowercase hex, computed in the browser before upload.
 *
 * The API uses it to refuse an exact duplicate before anything is sent to S3; the
 * ingestion worker hashes the real bytes again, so this is a fast, friendly check,
 * not the security boundary. Returns undefined where Web Crypto is unavailable
 * (it needs HTTPS or localhost), and the upload then simply goes ahead.
 */
export async function sha256Hex(file: Blob): Promise<string | undefined> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return undefined;
  try {
    const digest = await subtle.digest("SHA-256", await file.arrayBuffer());
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return undefined;
  }
}
