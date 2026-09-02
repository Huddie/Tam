import { useEffect, useMemo, useState } from "react";
import {
  type Discovery,
  type Project,
  type ProjectRef,
  assignProject,
  hideDiscovery,
  renameDiscovery,
  updateTags,
} from "./api";
import { useClickOutside } from "./useClickOutside";

const MAX_SUGGESTIONS = 8;

/** The single "manage this discovery" surface -- info, move-to-project,
 * tags, rename, and delete, all in one place instead of scattered across an
 * inline title-edit, a separate dropdown menu, and a project `<select>`
 * embedded in a table cell. Opened from the kebab button wherever a
 * discovery is shown (catalog row, detail page header); owns its own API
 * calls and reports results back via callbacks so the caller's list/detail
 * state stays in sync without a full refetch. */
export function DiscoveryManageModal({
  discovery,
  projects,
  allTags,
  onClose,
  onRenamed,
  onMoved,
  onTagsChanged,
  onDeleted,
}: {
  discovery: Discovery;
  projects: Project[];
  allTags: string[];
  onClose: () => void;
  onRenamed: (title: string) => void;
  onMoved: (project: ProjectRef | null) => void;
  onTagsChanged: (tags: string[]) => void;
  onDeleted: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [titleValue, setTitleValue] = useState(discovery.title);
  const [tagsValue, setTagsValue] = useState(discovery.tags);
  const [newTag, setNewTag] = useState("");
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useClickOutside<HTMLDivElement>(true, onClose);
  const tagBoxRef = useClickOutside<HTMLDivElement>(suggestionsOpen, () => setSuggestionsOpen(false));

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  // Existing tags matching what's typed so far (substring, case-insensitive),
  // minus whatever's already on this discovery -- re-adding an applied tag
  // is a no-op there's no reason to suggest. "Create <text>" is offered
  // alongside these whenever the typed text isn't an exact match for one of
  // them (server-side normalizeTag() -- collapsing case/punctuation variants
  // -- still applies once it's actually submitted; this is just deciding
  // whether to SHOW a "create" affordance, not replicating that logic).
  const normalizedInput = newTag.trim().toLowerCase();
  const suggestions = useMemo(
    () =>
      normalizedInput
        ? allTags.filter((t) => t.toLowerCase().includes(normalizedInput) && !tagsValue.includes(t)).slice(0, MAX_SUGGESTIONS)
        : [],
    [allTags, normalizedInput, tagsValue]
  );
  const alreadyApplied = tagsValue.some((t) => t.toLowerCase() === normalizedInput);
  const exactExistingMatch = allTags.some((t) => t.toLowerCase() === normalizedInput);
  const showCreateOption = normalizedInput.length > 0 && !alreadyApplied && !exactExistingMatch;

  async function saveRename() {
    const title = titleValue.trim();
    if (!title) return;
    setBusy(true);
    try {
      await renameDiscovery(discovery.id, title);
      onRenamed(title);
      setRenaming(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleMove(slug: string) {
    setBusy(true);
    try {
      await assignProject(discovery.id, slug || null);
      const project = slug ? (projects.find((p) => p.slug === slug) ?? null) : null;
      onMoved(project ? { id: project.id, slug: project.slug, name: project.name } : null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function persistTags(tags: string[]) {
    setBusy(true);
    try {
      const result = await updateTags(discovery.id, tags);
      setTagsValue(result.tags);
      onTagsChanged(result.tags);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function addTag(tag: string) {
    const trimmed = tag.trim();
    setNewTag("");
    setSuggestionsOpen(false);
    if (!trimmed || tagsValue.some((t) => t.toLowerCase() === trimmed.toLowerCase())) return;
    persistTags([...tagsValue, trimmed]);
  }

  function removeTag(tag: string) {
    persistTags(tagsValue.filter((t) => t !== tag));
  }

  // Enter picks the top suggestion if there's an existing tag matching what's
  // typed, otherwise it creates a brand new tag from the raw text -- same
  // choice clicking a suggestion vs. clicking "Create" makes.
  function handleTagInputEnter() {
    addTag(suggestions[0] ?? newTag);
  }

  async function handleDelete() {
    setBusy(true);
    try {
      await hideDiscovery(discovery.id);
      onDeleted();
      onClose();
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" ref={ref}>
        <div className="modal-header">
          {renaming ? (
            <div className="toolbar" style={{ margin: 0, flex: 1 }}>
              <input
                value={titleValue}
                onChange={(e) => setTitleValue(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && saveRename()}
                autoFocus
              />
              <button className="primary" disabled={!titleValue.trim() || busy} onClick={saveRename}>
                Save
              </button>
              <button
                className="secondary"
                onClick={() => {
                  setRenaming(false);
                  setTitleValue(discovery.title);
                }}
              >
                Cancel
              </button>
            </div>
          ) : (
            <>
              <h2 className="modal-title">{discovery.title}</h2>
              <button className="secondary" onClick={() => setRenaming(true)}>
                Rename
              </button>
            </>
          )}
          <button className="modal-close" aria-label="Close" onClick={onClose}>
            &times;
          </button>
        </div>

        {error && <p className="error">{error}</p>}

        <dl className="modal-info">
          <dt>Type</dt>
          <dd>{discovery.type}</dd>
          <dt>Created by</dt>
          <dd>{discovery.created_by}</dd>
          <dt>Updated</dt>
          <dd>{new Date(discovery.updated_at).toLocaleString()}</dd>
        </dl>

        <label className="modal-field">
          Project
          <select value={discovery.project?.slug ?? ""} disabled={busy} onChange={(e) => handleMove(e.target.value)}>
            <option value="">General</option>
            {projects.map((project) => (
              <option key={project.id} value={project.slug}>
                {project.name}
              </option>
            ))}
          </select>
        </label>

        <div className="modal-field">
          Tags
          <div className="modal-tags">
            {tagsValue.length ? (
              tagsValue.map((tag) => (
                <span className="tag tag-removable" key={tag}>
                  {tag}
                  <button
                    type="button"
                    className="tag-remove"
                    aria-label={`Remove tag ${tag}`}
                    disabled={busy}
                    onClick={() => removeTag(tag)}
                  >
                    &times;
                  </button>
                </span>
              ))
            ) : (
              <span className="muted">none</span>
            )}
          </div>
          <div className="tag-combobox" ref={tagBoxRef}>
            <input
              placeholder="Add a tag..."
              value={newTag}
              disabled={busy}
              onChange={(e) => {
                setNewTag(e.target.value);
                setSuggestionsOpen(true);
              }}
              onFocus={() => setSuggestionsOpen(true)}
              onKeyDown={(e) => e.key === "Enter" && handleTagInputEnter()}
            />
            {suggestionsOpen && (suggestions.length > 0 || showCreateOption) && (
              <div className="tag-suggestions">
                {suggestions.map((tag) => (
                  <button type="button" key={tag} onClick={() => addTag(tag)}>
                    {tag}
                  </button>
                ))}
                {showCreateOption && (
                  <button type="button" className="tag-suggestion-create" onClick={() => addTag(newTag)}>
                    Create &ldquo;{newTag.trim()}&rdquo;
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="modal-footer">
          {!confirmingDelete ? (
            <button className="danger" onClick={() => setConfirmingDelete(true)}>
              Delete
            </button>
          ) : (
            <>
              <span className="muted">Delete this discovery?</span>
              <button className="danger" disabled={busy} onClick={handleDelete}>
                Yes, delete
              </button>
              <button className="secondary" onClick={() => setConfirmingDelete(false)}>
                Cancel
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
