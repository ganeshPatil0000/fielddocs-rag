import { describe, expect, it } from "vitest";

import { FIBRE_CODE, contrastRatio, strand } from "./fibre";

describe("fibre colour code", () => {
  it("follows the standard 12-fibre order", () => {
    expect(FIBRE_CODE.map((f) => f.name)).toEqual([
      "Blue",
      "Orange",
      "Green",
      "Brown",
      "Slate",
      "White",
      "Red",
      "Black",
      "Yellow",
      "Violet",
      "Rose",
      "Aqua",
    ]);
  });

  it("maps source numbers to strands and wraps after 12", () => {
    expect(strand(1).name).toBe("Blue");
    expect(strand(2).name).toBe("Orange");
    expect(strand(13).name).toBe("Blue");
    expect(strand(0).name).toBe("Blue");
  });

  it("keeps every tag number readable (WCAG AA, 4.5:1)", () => {
    for (const fibre of FIBRE_CODE) {
      expect(contrastRatio(fibre.color, fibre.ink)).toBeGreaterThanOrEqual(4.5);
    }
  });
});
