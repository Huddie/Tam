import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { type BrowseResult, type YearEntry, browse, exportUrl, listSymbols, listYears } from "../api";

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

function BrowseTab({ prefix, onNavigate }: { prefix: string; onNavigate: (prefix: string) => void }) {
  const navigate = useNavigate();
  const [result, setResult] = useState<BrowseResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    browse(prefix)
      .then(setResult)
      .catch((e) => setError(String(e)));
  }, [prefix]);

  if (error) return <p className="error">{error}</p>;
  if (!result) return <p className="muted">Loading...</p>;

  return (
    <>
      <Breadcrumb prefix={prefix} onNavigate={onNavigate} />
      {prefix && (
        <div className="actions">
          <a className="secondary" href={exportUrl(prefix, "parquet")}>
            <button className="secondary">Export folder as .zip (Parquet)</button>
          </a>
          <a href={exportUrl(prefix, "csv")}>
            <button className="secondary">Export folder as CSV</button>
          </a>
        </div>
      )}
      <div className="browse-list">
        {result.prefixes.map((childPrefix) => (
          <div className="browse-row" key={childPrefix}>
            <a onClick={() => onNavigate(childPrefix)}>{childPrefix.replace(prefix, "")}</a>
            <span className="muted">folder</span>
          </div>
        ))}
        {result.objects.map((object) => (
          <div className="browse-row" key={object.key}>
            <a onClick={() => navigate(`/view?key=${encodeURIComponent(object.key)}`)}>{object.key.replace(prefix, "")}</a>
            <span className="muted">{(object.size / 1024).toFixed(1)} KB</span>
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
            <a href={exportUrl(`minute/${symbol}/`, "parquet")}>
              <button className="secondary">Export all years as .zip (Parquet)</button>
            </a>
            <a href={exportUrl(`minute/${symbol}/`, "csv")}>
              <button className="secondary">Export all years as CSV</button>
            </a>
          </div>
          <div className="browse-list">
            {years.map((y) => (
              <div className="browse-row" key={y.key}>
                <a onClick={() => navigate(`/view?key=${encodeURIComponent(y.key)}`)}>{y.year}</a>
                <span className="muted">{(y.size / 1024).toFixed(1)} KB</span>
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
