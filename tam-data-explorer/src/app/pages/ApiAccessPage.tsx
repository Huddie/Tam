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
        that doesn't work from a script. For curl/Python/notebooks, use an Access <strong>Service Token</strong>
        instead: a Client ID + Client Secret pair that authenticates non-interactively.
      </p>

      <h2>1. Create a Service Token</h2>
      <p>
        Cloudflare dashboard &rarr; <strong>Zero Trust</strong> &rarr; <strong>Access controls</strong> &rarr;{" "}
        <strong>Service Tokens</strong> &rarr; <strong>Create Service Token</strong>. Copy the Client ID and Client
        Secret it shows you (the secret is shown once).
      </p>
      <p>
        Then add a policy on this Application allowing it through: <strong>Access controls</strong> &rarr;{" "}
        <strong>Applications</strong> &rarr; the <code>data</code> application &rarr; <strong>Policies</strong> &rarr;
        add a policy with action <strong>Service Auth</strong>, include rule <strong>Service Token</strong> &rarr;
        select the one you just created.
      </p>

      <h2>2. curl</h2>
      <pre>
        <code>{`curl -H "CF-Access-Client-Id: <your-client-id>" \\
     -H "CF-Access-Client-Secret: <your-client-secret>" \\
     "https://data.tamquant.com/api/download?key=minute/AAPL/2024.parquet" \\
     -o AAPL_2024.parquet

# or as CSV:
curl -H "CF-Access-Client-Id: <your-client-id>" \\
     -H "CF-Access-Client-Secret: <your-client-secret>" \\
     "https://data.tamquant.com/api/file/csv?key=minute/AAPL/2024.parquet" \\
     -o AAPL_2024.csv`}</code>
      </pre>

      <h2>3. Python (plain requests)</h2>
      <pre>
        <code>{`import requests

headers = {
    "CF-Access-Client-Id": "<your-client-id>",
    "CF-Access-Client-Secret": "<your-client-secret>",
}
response = requests.get(
    "https://data.tamquant.com/api/download",
    params={"key": "minute/AAPL/2024.parquet"},
    headers=headers,
)
response.raise_for_status()
with open("AAPL_2024.parquet", "wb") as f:
    f.write(response.content)`}</code>
      </pre>

      <h2>4. From tam (tam.marketdata.explorer_client)</h2>
      <p className="muted">
        Same credential resolution order as <code>tam.discovery.auth.resolve_token()</code>: explicit kwarg &rarr;{" "}
        <code>DATA_EXPLORER_SERVICE_TOKEN_ID</code>/<code>DATA_EXPLORER_SERVICE_TOKEN_SECRET</code> env vars &rarr;
        Colab secret &rarr; <code>~/.config/tam-data-explorer/credentials.json</code>.
      </p>
      <pre>
        <code>{`from tam.marketdata.explorer_client import fetch_dataframe, download_csv

df = fetch_dataframe("AAPL", 2024)          # -> pandas DataFrame
download_csv("AAPL", 2024, "AAPL_2024.csv")  # -> saved straight to disk`}</code>
      </pre>
    </div>
  );
}
