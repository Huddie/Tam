import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { type DateIndex, type FilePage, csvDownloadUrl, fileDates, rawDownloadUrl, viewFile } from "../api";
import { useSort } from "../useSort";

const PAGE_SIZE = 50;
const YEAR_KEY_RE = /\/(\d{4})\.parquet$/;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** The "browse by month/day" folder view -- NOT a different physical
 * storage layout (tam.marketdata only ever writes one Parquet file per
 * symbol per year, see MARKETDATA.md), just a query-time grouping of the
 * same year file's `ts` column, decoded via /api/file/dates. Only months/
 * days that actually have rows show up, not a blind 1-12/1-31 grid. */
function DateBrowser({
  dateIndex,
  onSelectMonth,
  onSelectDay,
}: {
  dateIndex: DateIndex | null;
  onSelectMonth: (month: number) => void;
  onSelectDay: (month: number, day: number) => void;
}) {
  const [expandedMonth, setExpandedMonth] = useState<number | null>(null);

  if (!dateIndex) return <p className="browse-row muted">Loading...</p>;
  if (!dateIndex.months.length) return <p className="browse-row muted">No data.</p>;

  return (
    <>
      <p className="muted" style={{ fontSize: "0.85rem", margin: "0 0 0.5rem" }}>
        Grouped from the single year file below (no separate day/month files are stored) -- pick a month to view
        it as a whole, or expand it to pick one day.
      </p>
      <div className="browse-list">
        {dateIndex.months.map((m) => (
          <div key={m.month}>
            <div className="browse-row">
              <a onClick={() => setExpandedMonth(expandedMonth === m.month ? null : m.month)}>
                {expandedMonth === m.month ? "▾" : "▸"} {pad2(m.month)}/
              </a>
              <span className="size muted">
                {m.days.length} day{m.days.length === 1 ? "" : "s"}
              </span>
              <button className="secondary" onClick={() => onSelectMonth(m.month)}>
                View month
              </button>
            </div>
            {expandedMonth === m.month &&
              m.days.map((d) => (
                <div className="browse-row" key={d} style={{ paddingLeft: "2.5rem" }}>
                  <a onClick={() => onSelectDay(m.month, d)}>{pad2(d)}</a>
                </div>
              ))}
          </div>
        ))}
      </div>
    </>
  );
}

export function FileViewPage() {
  const [params, setParams] = useSearchParams();
  const key = params.get("key") ?? "";
  const page = Number(params.get("page") ?? "1");
  const month = params.get("month") ? Number(params.get("month")) : undefined;
  const day = params.get("day") ? Number(params.get("day")) : undefined;

  const [data, setData] = useState<FilePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [dateIndex, setDateIndex] = useState<DateIndex | null>(null);

  const yearMatch = YEAR_KEY_RE.exec(key);
  const year = yearMatch ? yearMatch[1] : "";

  useEffect(() => {
    if (!key) return;
    setData(null);
    viewFile(key, page, PAGE_SIZE, month, day)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [key, page, month, day]);

  useEffect(() => {
    if (!browsing || !key) return;
    setDateIndex(null);
    fileDates(key)
      .then(setDateIndex)
      .catch((e) => setError(String(e)));
  }, [browsing, key]);

  const { sorted, toggleSort, indicator } = useSort<Record<string, unknown>>(data?.rows ?? [], (row, column) => row[column]);

  function updateParams(mutate: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(params);
    mutate(next);
    setParams(next);
  }

  function goToPage(next: number) {
    updateParams((p) => p.set("page", String(next)));
  }

  function selectMonth(m: number) {
    updateParams((p) => {
      p.set("month", String(m));
      p.delete("day");
      p.set("page", "1");
    });
    setBrowsing(false);
  }

  function selectDay(m: number, d: number) {
    updateParams((p) => {
      p.set("month", String(m));
      p.set("day", String(d));
      p.set("page", "1");
    });
    setBrowsing(false);
  }

  function clearMonth() {
    updateParams((p) => {
      p.delete("month");
      p.delete("day");
      p.set("page", "1");
    });
  }

  function clearDay() {
    updateParams((p) => {
      p.delete("day");
      p.set("page", "1");
    });
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.totalRows / data.pageSize)) : 1;

  return (
    <div className="page page-wide">
      <Link className="back-link" to="/">
        &larr; Back to browse
      </Link>
      <h1>{key}</h1>

      {(month || browsing) && (
        <p className="breadcrumb">
          <a onClick={clearMonth}>{year}</a>
          {month != null && (
            <span>
              {" / "}
              {day != null ? <a onClick={clearDay}>{pad2(month)}</a> : pad2(month)}
            </span>
          )}
          {day != null && <span> / {pad2(day)}</span>}
        </p>
      )}

      <div className="actions">
        <a href={csvDownloadUrl(key)}>
          <button className="secondary">Download as CSV (all rows)</button>
        </a>
        <a href={rawDownloadUrl(key)}>
          <button className="secondary">Download original .parquet</button>
        </a>
        <button className="secondary" onClick={() => setBrowsing((v) => !v)}>
          {browsing ? "Cancel" : "Browse by month/day"}
        </button>
        {(month != null || day != null) && !browsing && (
          <button className="secondary" onClick={clearMonth}>
            View full year
          </button>
        )}
      </div>

      {browsing && (
        <DateBrowser dateIndex={dateIndex} onSelectMonth={selectMonth} onSelectDay={selectDay} />
      )}

      {error && <p className="error">{error}</p>}

      {!browsing && (
        <>
          {!data && !error && <p className="muted">Loading...</p>}

          {data && (
            <>
              <p className="muted">
                {data.totalRows.toLocaleString()} rows total -- showing page {data.page} of {totalPages}
              </p>
              <div className="pagination">
                <button className="pager-btn" disabled={page <= 1} onClick={() => goToPage(page - 1)}>
                  &lsaquo; Prev
                </button>
                <span className="muted">
                  Page {page} / {totalPages}
                </span>
                <button className="pager-btn" disabled={page >= totalPages} onClick={() => goToPage(page + 1)}>
                  Next &rsaquo;
                </button>
              </div>

              <div style={{ overflowX: "auto" }}>
                <table>
                  <thead>
                    <tr>
                      {data.columns.map((column) => (
                        <th className="sortable" key={column} onClick={() => toggleSort(column)}>
                          {column}
                          {indicator(column)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((row, index) => (
                      <tr key={index}>
                        {data.columns.map((column) => (
                          <td key={column}>{String(row[column] ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
