/**
 * Sources are colour-coded with the standard 12-fibre colour sequence (TIA-598)
 * that field engineers already read on every cable: source 1 is blue, 2 orange,
 * 3 green, and so on. The same colour marks a citation in the answer and its source.
 *
 * Shades are adjusted slightly so the number on each tag passes WCAG AA contrast.
 */

export interface Strand {
  name: string;
  /** Fill colour of the tag and the source's strand line. */
  color: string;
  /** Text colour on top of `color`. */
  ink: string;
}

const LIGHT_INK = "#FFFFFF";
const DARK_INK = "#15202A";

export const FIBRE_CODE: readonly Strand[] = [
  { name: "Blue", color: "#1F5FBF", ink: LIGHT_INK },
  { name: "Orange", color: "#E8730C", ink: DARK_INK },
  { name: "Green", color: "#237A4B", ink: LIGHT_INK },
  { name: "Brown", color: "#7A4B26", ink: LIGHT_INK },
  { name: "Slate", color: "#5E6E7B", ink: LIGHT_INK },
  { name: "White", color: "#FFFFFF", ink: DARK_INK },
  { name: "Red", color: "#C8372D", ink: LIGHT_INK },
  { name: "Black", color: "#1B1F23", ink: LIGHT_INK },
  { name: "Yellow", color: "#F2C230", ink: DARK_INK },
  { name: "Violet", color: "#6E4BB4", ink: LIGHT_INK },
  { name: "Rose", color: "#E58FA7", ink: DARK_INK },
  { name: "Aqua", color: "#2BB3C0", ink: DARK_INK },
];

/** Strand for a 1-based source number (13 wraps back to blue, like a 12-fibre tube). */
export function strand(index: number): Strand {
  const position = (((Math.max(1, Math.trunc(index)) - 1) % FIBRE_CODE.length) + FIBRE_CODE.length) % FIBRE_CODE.length;
  return FIBRE_CODE[position];
}

/** CSS custom properties consumed by .tag and .source in styles.css. */
export function strandStyle(index: number): Record<string, string> {
  const { color, ink } = strand(index);
  return { "--strand": color, "--strand-ink": ink };
}

// WCAG 2.x relative luminance and contrast ratio, used by the tests.
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
