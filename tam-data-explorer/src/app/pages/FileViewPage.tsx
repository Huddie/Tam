import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { type DateIndex, type FilePage, csvDownloadUrl, fileDates, rawDownloadUrl, viewFile } from "../api";
import { useSort } from "../useSort";

const PAGE_SIZE = 50;
const YEAR_KEY_RE = /\/(\d{4})\.parquet$/;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** A single "Download" button that opens CSV/Parquet as a small dropdown --
 * same pattern as BrowsePage's ExportDropdown ("Export this folder"), kept
 * as its own copy here rather than shared since it points at this page's
 * own /api/file/csv + /api/download routes instead of BrowsePage's
 * /api/export. Two separate top-level buttons for "download as CSV" and
 * "download original .parquet" was one button too many on an already
 * button-heavy toolbar. */
function DownloadDropdown({ fileKey }: { fileKey: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="export-dropdown">
      <button className="secondary" onClick={() => setOpen((value) => !value)}>
        Download &#9662;
      </button>
      {open && (
        <div className="export-dropdown-menu">
          <a href={csvDownloadUrl(fileKey)} onClick={() => setOpen(false)}>
            CSV (all rows)
          </a>
          <a href={rawDownloadUrl(fileKey)} onClick={() => setOpen(false)}>
            Original .parquet
          </a>
        </div>
      )}
    </div>
  );
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** The "browse by month/day, or pick a custom date range" panel -- both
 * live under the SAME toggle rather than each getting their own top-level
 * button, since they're really two ways to do the same thing (narrow the
 * page below to a subset of the year's rows). Plain text date inputs
 * (YYYY-MM-DD, matching this page's own URL param format directly) rather
 * than native <input type="date">: the OS-native date-picker chrome looks
 * out of place against the site's plain monospace styling everywhere
 * else, and a plain text input is already an existing, working, styled
 * component (same one "Search titles"/"Creator email" use) instead of a
 * one-off. */
function DateBrowser({
  dateIndex,
  rangeStart,
  rangeEnd,
  onSelectMonth,
  onSelectDay,
  onApplyRange,
  onClearRange,
}: {
  dateIndex: DateIndex | null;
  rangeStart: string;
  rangeEnd: string;
  onSelectMonth: (month: number) => void;
  onSelectDay: (month: number, day: number) => void;
  onApplyRange: (start: string, end: string) => void;
  onClearRange: () => void;
}) {
  const [expandedMonth, setExpandedMonth] = useState<number | null>(null);
  const [startInput, setStartInput] = useState(rangeStart);
  const [endInput, setEndInput] = useState(rangeEnd);
  const canApply = DATE_RE.test(startInput) && (!endInput || DATE_RE.test(endInput));

  return (
    <>
      <div className="toolbar">
        <span className="muted">Custom range:</span>
        <input placeholder="YYYY-MM-DD" value={startInput} onChange={(e) => setStartInput(e.target.value)} />
        <span className="muted">to</span>
        <input placeholder="YYYY-MM-DD (optional)" value={endInput} onChange={(e) => setEndInput(e.target.value)} />
        <button className="secondary" disabled={!canApply} onClick={() => onApplyRange(startInput, endInput)}>
          Apply
        </button>
        {(rangeStart || rangeEnd) && (
          <button
            className="secondary"
            onClick={() => {
              setStartInput("");
              setEndInput("");
              onClearRange();
            }}
          >
            Clear
          </button>
        )}
      </div>

      <p className="muted">
        Or pick a month/day below -- grouped from the single year file (no separate day/month files are stored).
      </p>
      <div className="browse-list">
        {!dateIndex && <p className="browse-row muted">Loading...</p>}
        {dateIndex && !dateIndex.months.length && <p className="browse-row muted">No data.</p>}
        {dateIndex?.months.map((m) => (
          <div key={m.month}>
            <div className="browse-row">
              <a onClick={() => setExpandedMonth(expandedMonth === m.month ? null : m.month)}>
                {expandedMonth === m.month ? "▾" : "▸"} {pad2(m.month)}/
              </a>
              <span className="size muted">
                {m.days.length} day{m.days.length === 1 ? "" : "s"}
              </span>
              <button className="secondary" onClick={() => onSelectMonth(m.month)}>
                View
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
  const rangeStart = params.get("start") ?? "";
  const rangeEnd = params.get("end") ?? "";

  const [data, setData] = useState<FilePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [dateIndex, setDateIndex] = useState<DateIndex | null>(null);
  const [pageInput, setPageInput] = useState(String(page));

  const yearMatch = YEAR_KEY_RE.exec(key);
  const year = yearMatch ? yearMatch[1] : "";

  useEffect(() => {
    if (!key) return;
    setData(null);
    viewFile(key, page, PAGE_SIZE, month, day, rangeStart || undefined, rangeEnd || undefined)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [key, page, month, day, rangeStart, rangeEnd]);

  useEffect(() => {
    setPageInput(String(page));
  }, [page]);

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
      p.delete("start");
      p.delete("end");
      p.set("page", "1");
    });
    setBrowsing(false);
  }

  function selectDay(m: number, d: number) {
    updateParams((p) => {
      p.set("month", String(m));
      p.set("day", String(d));
      p.delete("start");
      p.delete("end");
      p.set("page", "1");
    });
    setBrowsing(false);
  }

  function applyRange(start: string, end: string) {
    updateParams((p) => {
      p.set("start", start);
      if (end) p.set("end", end);
      else p.delete("end");
      p.delete("month");
      p.delete("day");
      p.set("page", "1");
    });
    setBrowsing(false);
  }

  function clearRange() {
    updateParams((p) => {
      p.delete("start");
      p.delete("end");
      p.set("page", "1");
    });
  }

  function clearMonth() {
    updateParams((p) => {
      p.delete("month");
      p.delete("day");
      p.delete("start");
      p.delete("end");
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
  const hasFilter = month != null || Boolean(rangeStart);

  function jumpToPage() {
    const target = Math.min(Math.max(1, Number(pageInput) || 1), totalPages);
    setPageInput(String(target));
    goToPage(target);
  }

  return (
    <div className="page page-wide">
      <Link className="back-link" to="/">
        &larr; Back to browse
      </Link>
      <h1>{key}</h1>

      {(hasFilter || browsing) && (
        <p className="breadcrumb">
          <a onClick={clearMonth}>{year}</a>
          {rangeStart && (
            <span>
              {" / "}
              {rangeStart}
              {rangeEnd && rangeEnd !== rangeStart ? ` → ${rangeEnd}` : ""}
            </span>
          )}
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
        <DownloadDropdown fileKey={key} />
        <button className="secondary" onClick={() => setBrowsing((v) => !v)}>
          {browsing ? "Cancel" : "Browse dates"}
        </button>
        {hasFilter && !browsing && (
          <button className="secondary" onClick={clearMonth}>
            View full year
          </button>
        )}
      </div>

      {browsing && (
        <DateBrowser
          dateIndex={dateIndex}
          rangeStart={rangeStart}
          rangeEnd={rangeEnd}
          onSelectMonth={selectMonth}
          onSelectDay={selectDay}
          onApplyRange={applyRange}
          onClearRange={clearRange}
        />
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
                {totalPages > 1 && (
                  <span className="muted" style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                    Go to
                    <input
                      type="number"
                      min={1}
                      max={totalPages}
                      value={pageInput}
                      onChange={(e) => setPageInput(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && jumpToPage()}
                      style={{ width: "4.5rem" }}
                    />
                    <button className="pager-btn" onClick={jumpToPage}>
                      Go
                    </button>
                  </span>
                )}
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
