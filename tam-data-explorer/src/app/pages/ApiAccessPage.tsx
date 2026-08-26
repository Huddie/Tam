import { Link } from "react-router-dom";

export function ApiAccessPage() {
  return (
    <div className="page">
      <Link className="back-link" to="/">
        &larr; Back to browse
      </Link>
      <h1>API access</h1>
      <p className="muted">
        Everything on this site is gated by Cloudflare Access, which normally means an interactive GitHub login --
        that doesn't work from a script. Instead, create your own <strong>personal token</strong> at{" "}
        <Link to="/settings/tokens">Personal tokens</Link>. It's yours alone -- revoking it never affects anyone
        else's access.
      </p>

      <h2>1. curl</h2>
      <pre>
        <code>{`curl -H "Authorization: Bearer <your-token>" \\
     "https://data.tamquant.com/api/token/download?key=minute/AAPL/2024.parquet" \\
     -o AAPL_2024.parquet

# or as CSV:
curl -H "Authorization: Bearer <your-token>" \\
     "https://data.tamquant.com/api/token/file/csv?key=minute/AAPL/2024.parquet" \\
     -o AAPL_2024.csv`}</code>
      </pre>

      <h2>2. Python (plain requests)</h2>
      <pre>
        <code>{`import requests

response = requests.get(
    "https://data.tamquant.com/api/token/download",
    params={"key": "minute/AAPL/2024.parquet"},
    headers={"Authorization": "Bearer <your-token>"},
)
response.raise_for_status()
with open("AAPL_2024.parquet", "wb") as f:
    f.write(response.content)`}</code>
      </pre>

      <h2>3. From tam (tam.marketdata.explorer_client)</h2>
      <p className="muted">
        Same credential resolution order as <code>tam.discovery.auth.resolve_token()</code>: explicit kwarg &rarr;{" "}
        <code>DATA_EXPLORER_TOKEN</code> env var &rarr; Colab secret (auto-detected, same name, nothing extra to
        configure) &rarr; <code>~/.config/tam-data-explorer/token</code>.
      </p>
      <pre>
        <code>{`from tam.marketdata.explorer_client import fetch_dataframe, download_csv

df = fetch_dataframe("AAPL", 2024)           # -> pandas DataFrame
download_csv("AAPL", 2024, "AAPL_2024.csv")  # -> saved straight to disk`}</code>
      </pre>

      <h2>4. Full SQL access (not just one file at a time)</h2>
      <p className="muted">
        <code>connect()</code> mints a short-lived, read-only R2 credential (scoped to just this bucket) behind the
        scenes and gives you a real SQL connection over the whole lake -- the same macros as{" "}
        <code>tam.marketdata.duckdb_query.open_duckdb()</code>, glob/multi-file queries included, without ever
        touching the real R2 account credentials. It refreshes itself automatically as the underlying credential
        approaches expiry, so a long notebook session keeps working.
      </p>
      <pre>
        <code>{`from tam.marketdata.explorer_client import connect

con = connect()
con.sql("SELECT * FROM daily_bars('AAPL') ORDER BY day").df()
con.sql("SELECT * FROM rolling_volatility('AAPL', 21) ORDER BY day").df()`}</code>
      </pre>
    </div>
  );
}
