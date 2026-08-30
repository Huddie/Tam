import { createContext, useContext, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  type BrowseResult,
  type BucketStats,
  type Dataset,
  type ExportSelection,
  type YearEntry,
  bucketStats,
  browse,
  exportUrl,
  listSymbols,
  listYears,
} from "../api";
import { Spinner } from "../Spinner";
import { useClickOutside } from "../useClickOutside";

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

/** Hive-style partition directory name ("key=value", e.g. this bucket's own
 * sec/facts/taxonomy=us-gaap/fiscal_year=2023/ layout) -- null for an
 * ordinary, non-partitioned folder name. Generic (not SEC-specific): any
 * dataset that adopts this same DuckDB/Parquet convention gets the same
 * cleaned-up display for free. */
function parseHivePartition(segment: string): { key: string; value: string } | null {
  const eq = segment.indexOf("=");
  if (eq <= 0) return null;
  return { key: segment.slice(0, eq), value: segment.slice(eq + 1) };
}

function formatPartitionKey(key: string): string {
  return key.replace(/_/g, "-");
}

function Breadcrumb({ prefix, onNavigate }: { prefix: string; onNavigate: (prefix: string) => void }) {
  const parts = prefix.split("/").filter(Boolean);
  return (
    <p className="breadcrumb">
      <a onClick={() => onNavigate("")}>tam-data</a>
      {parts.map((part, index) => {
        const partPrefix = parts.slice(0, index + 1).join("/") + "/";
        const partition = parseHivePartition(part);
        return (
          <span key={partPrefix}>
            {" / "}
            <a onClick={() => onNavigate(partPrefix)}>{partition ? partition.value : part}</a>
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
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false));
  const isEmpty = !(selection.prefixes?.length || selection.keys?.length);
  if (isEmpty) return null;

  return (
    <div className="export-dropdown" ref={ref}>
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

const SIZE_UNITS = ["auto", "KB", "MB", "GB"] as const;
type SizeUnit = (typeof SIZE_UNITS)[number];

function formatSize(bytes: number, unit: SizeUnit): string {
  const resolved = unit === "auto" ? (bytes >= 1024 ** 3 ? "GB" : bytes >= 1024 ** 2 ? "MB" : "KB") : unit;
  const divisor = resolved === "GB" ? 1024 ** 3 : resolved === "MB" ? 1024 ** 2 : 1024;
  return `${(bytes / divisor).toFixed(2)} ${resolved}`;
}

/** The size unit every SizeLabel on the page shares -- clicking ANY one of
 * them cycles auto -> KB -> MB -> GB for ALL of them at once, not just the
 * row that was clicked (a per-row unit made comparing rows across a table
 * confusing: two rows of a similar size showing "0.02 MB" and "20 KB" for
 * the same underlying magnitude). Defaults to "auto" -- each row still
 * picks its own best-fit unit independently until the user explicitly
 * overrides it by clicking one. */
const SizeUnitContext = createContext<[SizeUnit, (unit: SizeUnit) => void]>(["auto", () => {}]);

function useSizeUnit(): [SizeUnit, () => void] {
  const [unit, setUnit] = useContext(SizeUnitContext);
  const cycle = () => setUnit(SIZE_UNITS[(SIZE_UNITS.indexOf(unit) + 1) % SIZE_UNITS.length]);
  return [unit, cycle];
}

/** A file size that cycles auto -> KB -> MB -> GB on click -- "auto" (the
 * default) picks whichever unit reads best for that row's own magnitude;
 * clicking any SizeLabel on the page forces EVERY SizeLabel through the
 * same KB/MB/GB unit (see SizeUnitContext above), useful for comparing
 * rows of wildly different sizes on the same scale. */
function SizeLabel({ bytes }: { bytes: number }) {
  const [unit, cycle] = useSizeUnit();
  return (
    <span
      className="size muted"
      role="button"
      tabIndex={0}
      title="Click to cycle units (affects every size on this page)"
      onClick={(e) => {
        e.stopPropagation();
        cycle();
      }}
    >
      {formatSize(bytes, unit)}
    </span>
  );
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
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false));
  return (
    <div className="menu-dropdown" ref={ref}>
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
  if (!result) return <Spinner label="Loading..." />;

  // Folder entries are stored with their trailing "/" (from the API), so
  // they're distinguishable from file keys without a separate flag.
  const selectedPrefixes = [...selected].filter((item) => item.endsWith("/"));
  const selectedKeys = [...selected].filter((item) => !item.endsWith("/"));

  const visiblePrefixes = showHidden ? result.prefixes : result.prefixes.filter((p) => !isUnderscored(p));
  const visibleObjects = showHidden ? result.objects : result.objects.filter((o) => !isUnderscored(o.key));
  const hiddenCount = result.prefixes.length + result.objects.length - visiblePrefixes.length - visibleObjects.length;

  // If every visible folder at this level is the SAME Hive partition key
  // (e.g. all "fiscal_year=...") name the level once above the list instead
  // of repeating "fiscal_year=" on every single row -- a folder that isn't
  // partitioned this way, or a level mixing different keys, falls back to
  // showing each folder's full name as before.
  const prefixPartitions = visiblePrefixes.map((p) => parseHivePartition(basename(p)));
  const sharedPartitionKey =
    prefixPartitions.length > 0 && prefixPartitions.every((p) => p?.key === prefixPartitions[0]?.key)
      ? prefixPartitions[0]?.key ?? null
      : null;

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
        {sharedPartitionKey && <span className="tag">({formatPartitionKey(sharedPartitionKey)})</span>}
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
        {visiblePrefixes.map((childPrefix) => {
          const partition = parseHivePartition(basename(childPrefix));
          const label = sharedPartitionKey && partition ? partition.value : childPrefix.replace(prefix, "");
          return (
            <div className="browse-row" key={childPrefix}>
              {selecting && (
                <input type="checkbox" checked={selected.has(childPrefix)} onChange={() => toggleSelected(childPrefix)} />
              )}
              <a onClick={() => (selecting ? toggleSelected(childPrefix) : onNavigate(childPrefix))}>{label}</a>
              <span className="size muted">folder</span>
            </div>
          );
        })}
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
              <SizeLabel bytes={object.size} />
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
      <p className="muted">{result.total.toLocaleString()} items total</p>
      {(pageIndex > 0 || result.cursor) && (
        <div className="pagination">
          <button className="pager-btn" disabled={pageIndex === 0} onClick={() => setCursorStack((stack) => stack.slice(0, -1))}>
            &larr; Previous
          </button>
          <span className="muted">
            Page {pageIndex + 1} / {Math.max(1, Math.ceil(result.total / result.pageSize))}
          </span>
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
  const [dataset, setDataset] = useState<Dataset>("minute");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [symbol, setSymbol] = useState("");
  const [years, setYears] = useState<YearEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSymbol("");
    setQuery("");
    listSymbols(dataset)
      .then((r) => setSymbols(r.symbols))
      .catch((e) => setError(String(e)));
  }, [dataset]);

  useEffect(() => {
    if (!symbol) {
      setYears([]);
      return;
    }
    listYears(symbol, dataset)
      .then((r) => setYears(r.years))
      .catch((e) => setError(String(e)));
  }, [symbol, dataset]);

  if (error) return <p className="error">{error}</p>;

  // Client-side filter over the full symbol list -- thousands of short
  // ticker strings is a trivial amount of data to hold in memory and
  // filter on every keystroke, so there's no need for server-side search
  // (or pagination) here the way there is for browse()'s folder listings.
  const matches = query ? symbols.filter((s) => s.includes(query.toUpperCase())) : symbols;

  return (
    <>
      <div className="dataset-toggle">
        <button className={dataset === "minute" ? "active" : ""} onClick={() => setDataset("minute")}>
          Minute bars
        </button>
        <button className={dataset === "eod" ? "active" : ""} onClick={() => setDataset("eod")}>
          Daily / EOD bars
        </button>
      </div>
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
            <ExportDropdown selection={{ prefixes: [`${dataset}/${symbol}/`] }} label="Export all years" />
          </div>
          <div className="browse-list">
            {years.map((y) => (
              <div className="browse-row" key={y.key}>
                <TableIcon />
                <a onClick={() => navigate(`/view?key=${encodeURIComponent(y.key)}`)}>{y.year}</a>
                <SizeLabel bytes={y.size} />
              </div>
            ))}
            {!years.length && <p className="browse-row muted">No data for {symbol} yet.</p>}
          </div>
        </>
      )}
    </>
  );
}

/** Total storage/object-count summary for the whole bucket, broken down by
 * dataset -- shown on the bucket's landing page (root prefix, no folder
 * drilled into yet) so "how much are we storing" is visible at a glance
 * without digging through folders one at a time. Sizes use the SAME shared
 * unit (see SizeUnitContext) as every other size on the page. */
function BucketStatsPanel() {
  const [stats, setStats] = useState<BucketStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    bucketStats()
      .then(setStats)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return null; // a stats-fetch failure shouldn't block browsing itself
  if (!stats) return <Spinner label="Loading bucket stats..." />;

  const datasetLabels: Record<keyof BucketStats["byDataset"], string> = {
    minute: "Minute bars",
    eod: "Daily / EOD bars",
    other: "Other",
  };

  return (
    <div className="bucket-stats">
      <div className="bucket-stats-total">
        <SizeLabel bytes={stats.totalBytes} /> across {stats.totalObjects.toLocaleString()} object(s)
      </div>
      <div className="bucket-stats-breakdown">
        {(Object.keys(stats.byDataset) as Array<keyof BucketStats["byDataset"]>)
          .filter((key) => stats.byDataset[key].objects > 0)
          .map((key) => (
            <span key={key} className="bucket-stats-item">
              {datasetLabels[key]}: <SizeLabel bytes={stats.byDataset[key].bytes} /> ({stats.byDataset[key].objects.toLocaleString()})
            </span>
          ))}
      </div>
    </div>
  );
}

export function BrowsePage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "symbol" ? "symbol" : "browse";
  const prefix = params.get("prefix") ?? "";
  const sizeUnitState = useState<SizeUnit>("auto");

  function setTab(next: "browse" | "symbol") {
    setParams({ tab: next });
  }

  function navigatePrefix(next: string) {
    setParams({ tab: "browse", prefix: next });
  }

  return (
    <SizeUnitContext.Provider value={sizeUnitState}>
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

        {tab === "browse" && !prefix && <BucketStatsPanel />}
        {tab === "browse" ? <BrowseTab prefix={prefix} onNavigate={navigatePrefix} /> : <SymbolYearTab />}
      </div>
    </SizeUnitContext.Provider>
  );
}
