/** A small CSS-only spinner -- shown wherever a page/table fetch is still in
 * flight, so a slow load reads as "working", not "broken". Plain "Loading..."
 * text alone doesn't visually distinguish those two. Mirrors the Data
 * Explorer's own copy of this component (same "small independent pieces per
 * site" convention as useClickOutside.ts). */
export function Spinner({ label = "Loading..." }: { label?: string }) {
  return (
    <p className="loading-indicator muted">
      <span className="spinner" aria-hidden="true" />
      {label}
    </p>
  );
}
