import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  type CompletenessIndex,
  type DateIndex,
  type FilePage,
  csvDownloadUrl,
  fileCompleteness,
  fileDates,
  rawDownloadUrl,
  viewFile,
} from "../api";
import { Spinner } from "../Spinner";
import { useClickOutside } from "../useClickOutside";
import { useSort } from "../useSort";

const PAGE_SIZE = 50;
const YEAR_KEY_RE = /\/(\d{4})\.parquet$/;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** green/amber/red on the same rough thresholds tam.marketdata.validate
 * uses to decide "worth a warning" (50% -- below that is a red flag, not
 * just imperfect) plus a tighter top band, since "basically every minute
 * present" and "most of the session present" are both worth distinguishing
 * from "badly incomplete" at a glance. */
function completenessColor(ratio: number): string {
  if (ratio >= 0.98) return "#1a7f5a";
  if (ratio >= 0.5) return "#b45309";
  return "#b42318";
}

/** Sums actual/expected/extended-hours bar counts across every day in
 * `index` for which `include(dateStr)` (an "YYYY-MM-DD" string) returns
 * true -- the one piece of aggregation logic CompletenessBadge needs for
 * all four scopes (year/month/day/range), since a custom range can span
 * multiple months within the same year file. */
function sumCompleteness(
  index: CompletenessIndex,
  include: (dateStr: string) => boolean,
): { actual: number; expected: number; extended: number; days: number; incompleteDays: number } {
  let actual = 0;
  let expected = 0;
  let extended = 0;
  let days = 0;
  let incompleteDays = 0;
  for (const month of index.months) {
    for (const d of month.days) {
      const dateStr = `${index.year}-${pad2(month.month)}-${pad2(d.day)}`;
      if (include(dateStr)) {
        actual += d.actual_bars;
        expected += d.expected_bars;
        extended += d.extended_hours_bars;
        if (d.expected_bars > 0) {
          days += 1;
          if (d.actual_bars < d.expected_bars) incompleteDays += 1;
        }
      }
    }
  }
  return { actual, expected, extended, days, incompleteDays };
}

/** A minimal actual-vs-expected status indicator -- a colored dot plus a
 * percentage, scoped to whichever of year/month/day/custom-range is
 * currently being viewed. Click it for a small stats popover (session
 * completeness, trading days with any gap, extended-hours bar count);
 * hover alone still shows the same summary as a tooltip for a quick
 * glance without a click. Deliberately not a full day-by-day table here
 * -- that's what the "Browse dates" panel's own per-day list is for. */
function CompletenessBadge({
  index,
  month,
  day,
  rangeStart,
  rangeEnd,
}: {
  index: CompletenessIndex | null;
  month?: number;
  day?: number;
  rangeStart?: string;
  rangeEnd?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useClickOutside<HTMLSpanElement>(open, () => setOpen(false));
  if (!index) return null;

  let actual: number;
  let expected: number;
  let extended: number;
  let days: number;
  let incompleteDays: number;
  let label: string;

  if (rangeStart) {
    const end = rangeEnd || rangeStart;
    ({ actual, expected, extended, days, incompleteDays } = sumCompleteness(index, (d) => d >= rangeStart && d <= end));
    label = end !== rangeStart ? `${rangeStart} → ${end}` : rangeStart;
  } else if (month != null && day != null) {
    const dateStr = `${index.year}-${pad2(month)}-${pad2(day)}`;
    ({ actual, expected, extended, days, incompleteDays } = sumCompleteness(index, (d) => d === dateStr));
    label = dateStr;
  } else if (month != null) {
    const m = index.months.find((mo) => mo.month === month);
    actual = m?.actual_bars ?? 0;
    expected = m?.expected_bars ?? 0;
    extended = m?.extended_hours_bars ?? 0;
    days = m?.days.filter((d) => d.expected_bars > 0).length ?? 0;
    incompleteDays = m?.days.filter((d) => d.expected_bars > 0 && d.actual_bars < d.expected_bars).length ?? 0;
    label = `${index.year}-${pad2(month)}`;
  } else {
    actual = index.actual_bars;
    expected = index.expected_bars;
    extended = index.extended_hours_bars;
    days = index.months.flatMap((m) => m.days).filter((d) => d.expected_bars > 0).length;
    incompleteDays = index.months
      .flatMap((m) => m.days)
      .filter((d) => d.expected_bars > 0 && d.actual_bars < d.expected_bars).length;
    label = String(index.year);
  }

  if (expected === 0) return null; // no trading days at all in this scope -- nothing meaningful to show
  const ratio = actual / expected;
  const pct = Math.round(ratio * 100);
  const summary = `${label}: ${actual.toLocaleString()} / ${expected.toLocaleString()} bars present (${pct}%)`;

  return (
    <span className="completeness-badge-wrap" ref={ref}>
      <button
        className="completeness-badge"
        title={summary}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        <span className="completeness-dot" style={{ background: completenessColor(ratio) }} />
        {pct}% complete
      </button>
      {open && (
        <div className="completeness-popover" onClick={(e) => e.stopPropagation()}>
          <p className="mono">{label}</p>
          <dl>
            <dt>Regular session</dt>
            <dd>
              {actual.toLocaleString()} / {expected.toLocaleString()} bars ({pct}%)
            </dd>
            <dt>Trading days</dt>
            <dd>
              {days.toLocaleString()} ({incompleteDays.toLocaleString()} with a gap)
            </dd>
            <dt>Extended hours</dt>
            <dd>{extended.toLocaleString()} bars (pre/post-market, not counted above)</dd>
          </dl>
        </div>
      )}
    </span>
  );
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
  const ref = useClickOutside<HTMLDivElement>(open, () => setOpen(false));
  return (
    <div className="export-dropdown" ref={ref}>
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

/** The "browse by month/day, or pick a custom date range" panel -- both
 * live under the SAME toggle rather than each getting their own top-level
 * button, since they're really two ways to do the same thing (narrow the
 * page below to a subset of the year's rows). */
function DateBrowser({
  dateIndex,
  year,
  rangeStart,
  rangeEnd,
  onSelectMonth,
  onSelectDay,
  onApplyRange,
  onClearRange,
}: {
  dateIndex: DateIndex | null;
  year: string;
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
  const bounds = { min: `${year}-01-01`, max: `${year}-12-31` };

  return (
    <>
      <div className="toolbar">
        <span className="muted">Custom range:</span>
        <input type="date" value={startInput} min={bounds.min} max={bounds.max} onChange={(e) => setStartInput(e.target.value)} />
        <span className="muted">to</span>
        <input
          type="date"
          value={endInput}
          min={startInput || bounds.min}
          max={bounds.max}
          onChange={(e) => setEndInput(e.target.value)}
        />
        <button className="secondary" disabled={!startInput} onClick={() => onApplyRange(startInput, endInput)}>
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
        <small>Or pick a month/day below -- grouped from the single year file (no separate day/month files are stored).</small>
      </p>
      <div className="browse-list">
        {!dateIndex && <Spinner label="Loading dates..." />}
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
  const [completeness, setCompleteness] = useState<CompletenessIndex | null>(null);
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
    if (!key) return;
    setCompleteness(null);
    fileCompleteness(key).then(setCompleteness);
  }, [key]);

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
      <CompletenessBadge index={completeness} month={month} day={day} rangeStart={rangeStart} rangeEnd={rangeEnd} />

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
          year={year}
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
          {!data && !error && <Spinner label="Loading table..." />}

          {data && (
            <>
              <p className="muted">{data.totalRows.toLocaleString()} rows total</p>
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

              <div className="table-wrap">
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
