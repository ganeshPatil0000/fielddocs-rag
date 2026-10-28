import { FIBRE_CODE } from "../lib/fibre";

/** A 12-fibre ribbon in the standard colour order: the product's one decorative mark. */
export function FibreRibbon({ className = "ribbon" }: { className?: string }) {
  return (
    <div className={className} aria-hidden="true">
      {FIBRE_CODE.map((fibre) => (
        <span key={fibre.name} style={{ background: fibre.color }} />
      ))}
    </div>
  );
}

/** Logo: a fibre seen end-on (coating, cladding, core). */
export function FibreMark() {
  return (
    <svg className="mark" viewBox="0 0 32 32" aria-hidden="true">
      <circle cx="16" cy="16" r="14" fill="var(--action)" />
      <circle cx="16" cy="16" r="9" fill="var(--surface)" />
      <circle cx="16" cy="16" r="3.5" fill="var(--action)" />
    </svg>
  );
}
