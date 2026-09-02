/** Shared inline icons -- kept in one place since more than one component
 * (the manage-modal trigger today, potentially more later) needs the same
 * kebab glyph, drawn ourselves rather than relying on a font's own "..."
 * glyph (renders cramped/uneven depending on font/OS) or an icon library
 * for one icon. */
export function KebabIcon() {
  return (
    <svg width="16" height="4" viewBox="0 0 16 4" fill="currentColor" aria-hidden="true">
      <circle cx="2" cy="2" r="2" />
      <circle cx="8" cy="2" r="2" />
      <circle cx="14" cy="2" r="2" />
    </svg>
  );
}
