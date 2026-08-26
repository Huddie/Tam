import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { type BrowseResult, type ExportSelection, type YearEntry, browse, exportUrl, listSymbols, listYears } from "../api";

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

function BrowseTab({ prefix, onNavigate }: { prefix: string; onNavigate: (prefix: string) => void }) {
  const navigate = useNavigate();
  const [result, setResult] = useState<BrowseResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    setSelecting(false);
    setSelected(new Set());
    browse(prefix)
      .then(setResult)
      .catch((e) => setError(String(e)));
  }, [prefix]);

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
      </div>

      <div className="browse-list">
        {result.prefixes.map((childPrefix) => (
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
        {result.objects.map((object) => (
          <div className="browse-row" key={object.key}>
            {selecting && <input type="checkbox" checked={selected.has(object.key)} onChange={() => toggleSelected(object.key)} />}
            <a
              onClick={() =>
                selecting ? toggleSelected(object.key) : navigate(`/view?key=${encodeURIComponent(object.key)}`)
              }
            >
              {object.key.replace(prefix, "")}
            </a>
            <span className="size muted">{(object.size / 1024).toFixed(1)} KB</span>
          </div>
        ))}
        {!result.prefixes.length && !result.objects.length && <p className="browse-row muted">(empty)</p>}
      </div>
      {result.truncated && <p className="muted">Showing a partial listing -- narrow the folder to see everything.</p>}
    </>
  );
}

function SymbolYearTab() {
  const navigate = useNavigate();
  const [symbols, setSymbols] = useState<string[]>([]);
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

  return (
    <>
      <div className="toolbar">
        <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
          <option value="">Pick a symbol...</option>
          {symbols.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      {symbol && (
        <>
          <div className="actions">
            <ExportDropdown selection={{ prefixes: [`minute/${symbol}/`] }} label="Export all years" />
          </div>
          <div className="browse-list">
            {years.map((y) => (
              <div className="browse-row" key={y.key}>
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
