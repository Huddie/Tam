import { useMemo, useState } from "react";

export type SortDir = "asc" | "desc";

/** Client-side column sort for an already-loaded row array -- click a
 * column header once for ascending, again for descending, a different
 * column to switch to it (starting ascending again). `getValue` maps a row
 * + column key to whatever comparable value that column represents; missing
 * values (null/undefined) always sort last regardless of direction. */
export function useSort<T>(rows: T[], getValue: (row: T, key: string) => unknown) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  function toggleSort(key: string) {
    if (sortKey === key) {
      setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = getValue(a, sortKey);
      const bv = getValue(b, sortKey);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [rows, sortKey, sortDir, getValue]);

  function indicator(key: string): string {
    if (sortKey !== key) return "";
    return sortDir === "asc" ? " ▲" : " ▼";
  }

  return { sorted, toggleSort, indicator };
}
