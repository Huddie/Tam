import { useState } from "react";

/** Three evenly-spaced dots, drawn ourselves rather than relying on a font's
 * own "..." glyph (which renders cramped, with uneven spacing depending on
 * the font/OS) or pulling in an icon library for one icon. */
function KebabIcon() {
  return (
    <svg width="16" height="4" viewBox="0 0 16 4" fill="currentColor" aria-hidden="true">
      <circle cx="2" cy="2" r="2" />
      <circle cx="8" cy="2" r="2" />
      <circle cx="14" cy="2" r="2" />
    </svg>
  );
}

/** The "..." menu shown next to a discovery's title (catalog table row, or
 * the detail page's header) -- Rename and Delete, both creator-only (the
 * server enforces this too; callers only render this when they already
 * know can_manage is true). Delete is soft (see hideDiscovery() on the
 * Worker side) -- this menu's own confirm step is the only "are you sure"
 * a user gets, so it needs to be unambiguous about what actually happens. */
export function ManageMenu({ onRename, onDelete }: { onRename: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  return (
    <div className="menu-dropdown">
      <button
        className="menu-dropdown-toggle"
        aria-label="More actions"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((value) => !value);
          setConfirmingDelete(false);
        }}
      >
        <KebabIcon />
      </button>
      {open && (
        <div className="menu-dropdown-menu" onClick={(e) => e.stopPropagation()}>
          {!confirmingDelete ? (
            <>
              <button
                onClick={() => {
                  setOpen(false);
                  onRename();
                }}
              >
                Rename
              </button>
              <button className="danger" onClick={() => setConfirmingDelete(true)}>
                Delete
              </button>
            </>
          ) : (
            <>
              <button
                className="danger"
                onClick={() => {
                  setOpen(false);
                  onDelete();
                }}
              >
                Yes, delete
              </button>
              <button onClick={() => setConfirmingDelete(false)}>Cancel</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
