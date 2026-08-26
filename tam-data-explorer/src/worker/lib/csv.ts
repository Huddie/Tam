/** Plain CSV serialization -- values with a comma/quote/newline get
 * quoted (doubling embedded quotes), everything else is written bare.
 * Dates are rendered via toISOString() rather than their default
 * `Date.toString()` (which is locale-dependent and awkward in a CSV). */
function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = value instanceof Date ? value.toISOString() : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function rowsToCsv(columns: string[], rows: Record<string, unknown>[]): string {
  const lines = [columns.map(csvCell).join(",")];
  for (const row of rows) {
    lines.push(columns.map((column) => csvCell(row[column])).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}
