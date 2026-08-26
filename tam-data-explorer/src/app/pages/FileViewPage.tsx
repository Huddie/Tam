import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { type FilePage, csvDownloadUrl, rawDownloadUrl, viewFile } from "../api";
import { useSort } from "../useSort";

const PAGE_SIZE = 50;

export function FileViewPage() {
  const [params, setParams] = useSearchParams();
  const key = params.get("key") ?? "";
  const page = Number(params.get("page") ?? "1");
  const [data, setData] = useState<FilePage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!key) return;
    setData(null);
    viewFile(key, page, PAGE_SIZE)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [key, page]);

  const { sorted, toggleSort, indicator } = useSort<Record<string, unknown>>(data?.rows ?? [], (row, column) => row[column]);

  function goToPage(next: number) {
    const nextParams = new URLSearchParams(params);
    nextParams.set("page", String(next));
    setParams(nextParams);
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.totalRows / data.pageSize)) : 1;

  return (
    <div className="page page-wide">
      <Link className="back-link" to="/">
        &larr; Back to browse
      </Link>
      <h1>{key}</h1>

      <div className="actions">
        <a href={csvDownloadUrl(key)}>
          <button className="secondary">Download as CSV (all rows)</button>
        </a>
        <a href={rawDownloadUrl(key)}>
          <button className="secondary">Download original .parquet</button>
        </a>
      </div>

      {error && <p className="error">{error}</p>}
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
    </div>
  );
}
