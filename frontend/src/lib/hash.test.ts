// @vitest-environment node
import { describe, expect, it } from "vitest";

import { sha256Hex } from "./hash";

describe("sha256Hex", () => {
  it("matches the SHA-256 the server computes", async () => {
    // Same value as: printf 'abc' | sha256sum
    expect(await sha256Hex(new Blob(["abc"]))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });
});
