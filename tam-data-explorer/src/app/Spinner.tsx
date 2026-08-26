/** A small CSS-only spinner -- shown wherever a table/listing fetch is still
 * in flight, so a slow big-file load reads as "working", not "broken".
 * Plain "Loading..." text alone doesn't visually distinguish those two. */
export function Spinner({ label = "Loading..." }: { label?: string }) {
  return (
    <p className="loading-indicator muted">
      <span className="spinner" aria-hidden="true" />
      {label}
    </p>
  );
}
