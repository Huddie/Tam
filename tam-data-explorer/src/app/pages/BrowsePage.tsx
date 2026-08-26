import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { type BrowseResult, type ExportSelection, type YearEntry, browse, exportUrl, listSymbols, listYears } from "../api";

/** A small hand-drawn "table" glyph (a grid, not a real icon library) --
 * marks a row as "this opens the tabular/table view when clicked",
 * distinguishing a .parquet file's row from a plain folder row at a
 * glance without relying on file extension text alone (which the "Show
 * extensions" toggle below can hide). */
function TableIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      aria-hidden="true"
      style={{ flexShrink: 0 }}
    >
      <rect x="1" y="1" width="12" height="12" rx="1" />
      <line x1="1" y1="5.3" x2="13" y2="5.3" />
      <line x1="1" y1="9.6" x2="13" y2="9.6" />
      <line x1="5.5" y1="1" x2="5.5" y2="13" />
    </svg>
  );
}

function Breadcrumb({ prefix, onNavigate }: { prefix: string; onNavigate: (prefix: string) => void }) {
  const parts = prefix.split("/").filter(Boolean);
  return (
    <p className="breadcrumb">
      <a onClick={() => onNavigate("")}>tam-data</a>
      {parts.map((part, index) => {
        const partPrefix = parts.slice(0, index + 1).join("/") + "/";
        return (
          <span key={partPrefix}>
            {" / "}
            <a onClick={() => onNavigate(partPrefix)}>{part}</a>
          </span>
        );
      })}
    </p>
  );
}

/** A button that opens a small menu with CSV/Parquet export options for
 * whatever `selection` (folders and/or specific files) is currently in
 * play -- shared by the per-folder quick-export action and the
 * selection-mode "export selected" action below. */
function ExportDropdown({ selection, label }: { selection: ExportSelection; label: string }) {
  const [open, setOpen] = useState(false);
  const isEmpty = !(selection.prefixes?.length || selection.keys?.length);
  if (isEmpty) return null;

  return (
    <div className="export-dropdown">
      <button className="secondary" onClick={() => setOpen((value) => !value)}>
        {label} &#9662;
      </button>
      {open && (
        <div className="export-dropdown-menu">
          <a href={exportUrl(selection, "csv")} onClick={() => setOpen(false)}>
            Export as CSV
          </a>
          <a href={exportUrl(selection, "parquet")} onClick={() => setOpen(false)}>
            Export as Parquet (.zip)
          </a>
        </div>
      )}
    </div>
  );
}

function basename(key: string): string {
  return key.replace(/\/$/, "").split("/").pop() ?? key;
}

/** A "starts with an underscore" folder/file (e.g. _diag/, _test/, the
 * ingestion manifest _manifest.json) -- a plain naming convention this
 * bucket already uses for "not part of the actual symbol data", not a
 * server-enforced concept (see browse.ts's own comment on why that
 * filtering stays server-side only for .completeness.json, not this). */
function isUnderscored(key: string): boolean {
  return basename(key).startsWith("_");
}

/** The "[Options]" dropdown -- Show extensions / Show hidden, both off by
 * default. Kept as one small menu instead of two standalone checkboxes on
 * the actions row now that there are two of them -- matches ManageMenu's
 * own "one button, not a growing row of toggles" reasoning on the
 * Discovery side. */
function OptionsMenu({
  showExtensions,
  setShowExtensions,
  showHidden,
  setShowHidden,
}: {
  showExtensions: boolean;
  setShowExtensions: (value: boolean) => void;
  showHidden: boolean;
  setShowHidden: (value: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="menu-dropdown">
      <button
        className="secondary"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        Options &#9662;
      </button>
      {open && (
        <div className="menu-dropdown-menu menu-dropdown-menu-checkboxes" onClick={(e) => e.stopPropagation()}>
          <label>
            <input type="checkbox" checked={showExtensions} onChange={(e) => setShowExtensions(e.target.checked)} />
            Show extensions
          </label>
          <label>
            <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
            Show hidden (_-prefixed)
          </label>
        </div>
      )}
    </div>
  );
}

function BrowseTab({ prefix, onNavigate }: { prefix: string; onNavigate: (prefix: string) => void }) {
  const navigate = useNavigate();
  const [result, setResult] = useState<BrowseResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Both off by default -- the .parquet suffix is redundant noise once the
  // table icon already marks a row as "this is a table", and _-prefixed
  // folders/files are internal-convention clutter (diagnostics, the
  // ingestion manifest) that isn't useful to browse day to day.
  const [showExtensions, setShowExtensions] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  // Stack of cursors seen so far, one per page already visited -- lets
  // "Previous" go back without a second round-trip (R2's cursors are
  // forward-only, so the way back is replaying cursors we already have,
  // not asking R2 for a "previous page" that doesn't exist as a concept).
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([undefined]);
  const pageIndex = cursorStack.length - 1;

  useEffect(() => {
    setSelecting(false);
    setSelected(new Set());
    setCursorStack([undefined]);
  }, [prefix]);

  useEffect(() => {
    setSelecting(false);
    setSelected(new Set());
    browse(prefix, cursorStack[pageIndex])
      .then(setResult)
      .catch((e) => setError(String(e)));
  }, [prefix, cursorStack, pageIndex]);

  function toggleSelected(item: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(item)) next.delete(item);
      else next.add(item);
      return next;
    });
  }

  if (error) return <p className="error">{error}</p>;
  if (!result) return <p className="muted">Loading...</p>;

  // Folder entries are stored with their trailing "/" (from the API), so
  // they're distinguishable from file keys without a separate flag.
  const selectedPrefixes = [...selected].filter((item) => item.endsWith("/"));
  const selectedKeys = [...selected].filter((item) => !item.endsWith("/"));

  const visiblePrefixes = showHidden ? result.prefixes : result.prefixes.filter((p) => !isUnderscored(p));
  const visibleObjects = showHidden ? result.objects : result.objects.filter((o) => !isUnderscored(o.key));
  const hiddenCount = result.prefixes.length + result.objects.length - visiblePrefixes.length - visibleObjects.length;

  return (
    <>
      <Breadcrumb prefix={prefix} onNavigate={onNavigate} />

      <div className="actions">
        <button
          className="secondary"
          onClick={() => {
            setSelecting((value) => !value);
            setSelected(new Set());
          }}
        >
          {selecting ? "Cancel selection" : "Select..."}
        </button>
        {selecting ? (
          <ExportDropdown
            selection={{ prefixes: selectedPrefixes, keys: selectedKeys }}
            label={`Export selected (${selected.size})`}
          />
        ) : (
          prefix && <ExportDropdown selection={{ prefixes: [prefix] }} label="Export this folder" />
        )}
        <div style={{ marginLeft: "auto" }}>
          <OptionsMenu
            showExtensions={showExtensions}
            setShowExtensions={setShowExtensions}
            showHidden={showHidden}
            setShowHidden={setShowHidden}
          />
        </div>
      </div>

      <div className="browse-list">
        {visiblePrefixes.map((childPrefix) => (
          <div className="browse-row" key={childPrefix}>
            {selecting && (
              <input type="checkbox" checked={selected.has(childPrefix)} onChange={() => toggleSelected(childPrefix)} />
            )}
            <a onClick={() => (selecting ? toggleSelected(childPrefix) : onNavigate(childPrefix))}>
              {childPrefix.replace(prefix, "")}
            </a>
            <span className="size muted">folder</span>
          </div>
        ))}
        {visibleObjects.map((object) => {
          const isTable = object.key.endsWith(".parquet");
          const name = object.key.replace(prefix, "");
          const displayName = !showExtensions && isTable ? name.replace(/\.parquet$/, "") : name;
          return (
            <div className="browse-row" key={object.key}>
              {selecting && <input type="checkbox" checked={selected.has(object.key)} onChange={() => toggleSelected(object.key)} />}
              {isTable && <TableIcon />}
              <a
                onClick={() =>
                  selecting ? toggleSelected(object.key) : navigate(`/view?key=${encodeURIComponent(object.key)}`)
                }
              >
                {displayName}
              </a>
              <span className="size muted">{(object.size / 1024).toFixed(1)} KB</span>
            </div>
          );
        })}
        {!visiblePrefixes.length && !visibleObjects.length && <p className="browse-row muted">(empty)</p>}
      </div>
      {hiddenCount > 0 && (
        <p className="muted" style={{ fontSize: "0.8rem", margin: "0.5rem 0 0" }}>
          <small>{hiddenCount} hidden -- toggle "Show hidden" in Options to see them.</small>
        </p>
      )}
      {(pageIndex > 0 || result.cursor) && (
        <div className="pagination">
          <button className="pager-btn" disabled={pageIndex === 0} onClick={() => setCursorStack((stack) => stack.slice(0, -1))}>
            &larr; Previous
          </button>
          <span className="muted">Page {pageIndex + 1}</span>
          <button
            className="pager-btn"
            disabled={!result.cursor}
            onClick={() => setCursorStack((stack) => [...stack, result.cursor ?? undefined])}
          >
            Next &rarr;
          </button>
        </div>
      )}
    </>
  );
}

function SymbolYearTab() {
  const navigate = useNavigate();
  const [symbols, setSymbols] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [symbol, setSymbol] = useState("");
  const [years, setYears] = useState<YearEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listSymbols()
      .then((r) => setSymbols(r.symbols))
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!symbol) {
      setYears([]);
      return;
    }
    listYears(symbol)
      .then((r) => setYears(r.years))
      .catch((e) => setError(String(e)));
  }, [symbol]);

  if (error) return <p className="error">{error}</p>;

  // Client-side filter over the full symbol list -- thousands of short
  // ticker strings is a trivial amount of data to hold in memory and
  // filter on every keystroke, so there's no need for server-side search
  // (or pagination) here the way there is for browse()'s folder listings.
  const matches = query ? symbols.filter((s) => s.includes(query.toUpperCase())) : symbols;

  return (
    <>
      <div className="toolbar">
        <input
          placeholder={`Search ${symbols.length} symbols...`}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSymbol("");
          }}
        />
      </div>
      {!symbol && (
        <div className="browse-list" style={{ maxHeight: "320px", overflowY: "auto" }}>
          {matches.slice(0, 200).map((s) => (
            <div className="browse-row" key={s}>
              <a onClick={() => setSymbol(s)}>{s}</a>
            </div>
          ))}
          {!matches.length && <p className="browse-row muted">No symbols match "{query}".</p>}
          {matches.length > 200 && (
            <p className="browse-row muted">{matches.length - 200} more match -- keep typing to narrow it down.</p>
          )}
        </div>
      )}
      {symbol && (
        <>
          <p className="breadcrumb">
            <a onClick={() => setSymbol("")}>&larr; {symbol}</a>
          </p>
          <div className="actions">
            <ExportDropdown selection={{ prefixes: [`minute/${symbol}/`] }} label="Export all years" />
          </div>
          <div className="browse-list">
            {years.map((y) => (
              <div className="browse-row" key={y.key}>
                <TableIcon />
                <a onClick={() => navigate(`/view?key=${encodeURIComponent(y.key)}`)}>{y.year}</a>
                <span className="size muted">{(y.size / 1024).toFixed(1)} KB</span>
              </div>
            ))}
            {!years.length && <p className="browse-row muted">No data for {symbol} yet.</p>}
          </div>
        </>
      )}
    </>
  );
}

export function BrowsePage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "symbol" ? "symbol" : "browse";
  const prefix = params.get("prefix") ?? "";

  function setTab(next: "browse" | "symbol") {
    setParams({ tab: next });
  }

  function navigatePrefix(next: string) {
    setParams({ tab: "browse", prefix: next });
  }

  return (
    <div className="page page-wide">
      <header className="page-header">
        <h1>Data Explorer</h1>
        <nav>
          <Link to="/settings/tokens">Personal tokens</Link>
          <Link to="/api-access">API access</Link>
        </nav>
      </header>

      <div className="tabs">
        <button className={tab === "browse" ? "active" : ""} onClick={() => setTab("browse")}>
          Browse
        </button>
        <button className={tab === "symbol" ? "active" : ""} onClick={() => setTab("symbol")}>
          Symbol / Year
        </button>
      </div>

      {tab === "browse" ? <BrowseTab prefix={prefix} onNavigate={navigatePrefix} /> : <SymbolYearTab />}
    </div>
  );
}
