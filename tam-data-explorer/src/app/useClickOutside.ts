import { type RefObject, useEffect, useRef } from "react";

/** Closes an open popover/dropdown when the user clicks anywhere outside
 * it -- the standard "click away to dismiss" behavior every popover here
 * (ExportDropdown, OptionsMenu, DownloadDropdown, the completeness stats
 * popover, ...) should have, not just "click the same toggle again" or a
 * specific action inside it.
 *
 * Listens on `mousedown` rather than `click` deliberately: a toggle
 * button's own onClick (which flips `open`) fires on the LATER `click`
 * event, so by the time it runs, this hook's `mousedown` listener has
 * already checked "was that inside the ref?" -- for a click on the
 * toggle button itself (inside the ref, since the ref wraps toggle+menu
 * together), this hook does nothing and the toggle's own onClick is what
 * opens/closes it; only a click genuinely OUTSIDE both fires `onOutside`.
 * Attach the returned ref to the dropdown's outer wrapper (toggle button
 * + menu together), and only while `open` is true (the effect below is a
 * no-op otherwise, so closed dropdowns don't pay for a listener). */
export function useClickOutside<T extends HTMLElement>(open: boolean, onOutside: () => void): RefObject<T> {
  const ref = useRef<T>(null);

  useEffect(() => {
    if (!open) return;

    function handlePointerDown(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onOutside();
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [open, onOutside]);

  return ref;
}
