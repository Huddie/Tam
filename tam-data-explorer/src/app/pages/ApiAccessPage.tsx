import { useState } from "react";
import { Link } from "react-router-dom";

type Token = { text: string; cls?: string };

/** A tiny hand-rolled tokenizer, not a real language parser -- good enough
 * to color comments/strings/keywords/placeholders for the handful of
 * short snippets on this page without pulling in a whole syntax-highlighter
 * dependency for something this small. */
function tokenize(code: string, pattern: RegExp, classify: (match: RegExpExecArray) => string): Token[] {
  const tokens: Token[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(code))) {
    if (match.index > last) tokens.push({ text: code.slice(last, match.index) });
    tokens.push({ text: match[0], cls: classify(match) });
    last = match.index + match[0].length;
  }
  if (last < code.length) tokens.push({ text: code.slice(last) });
  return tokens;
}

const BASH_PATTERN = /(#.*$)|("(?:[^"\\]|\\.)*")|(<[a-zA-Z0-9_-]+>)/gm;
const PYTHON_PATTERN =
  /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|\b(import|from|def|return|with|as|True|False|None)\b/gm;

function highlight(code: string, language: "bash" | "python"): Token[] {
  if (language === "bash") {
    return tokenize(code, BASH_PATTERN, (m) => (m[1] ? "tok-comment" : m[2] ? "tok-string" : "tok-placeholder"));
  }
  return tokenize(code, PYTHON_PATTERN, (m) => (m[1] ? "tok-comment" : m[2] ? "tok-string" : "tok-keyword"));
}

function CodeBlock({ code, language }: { code: string; language: "bash" | "python" }) {
  const [copied, setCopied] = useState(false);
  const tokens = highlight(code, language);

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="mono muted">{language}</span>
        <button
          className="code-block-copy"
          onClick={() => {
            navigator.clipboard.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <pre>
        <code>
          {tokens.map((token, i) => (
            <span key={i} className={token.cls}>
              {token.text}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}

const CURL_SNIPPET = `curl -H "Authorization: Bearer <your-token>" \\
     "https://data.tamquant.com/api/token/download?key=minute/AAPL/2024.parquet" \\
     -o AAPL_2024.parquet

# or as CSV:
curl -H "Authorization: Bearer <your-token>" \\
     "https://data.tamquant.com/api/token/file/csv?key=minute/AAPL/2024.parquet" \\
     -o AAPL_2024.csv`;

const PYTHON_SNIPPET = `import requests

response = requests.get(
    "https://data.tamquant.com/api/token/download",
    params={"key": "minute/AAPL/2024.parquet"},
    headers={"Authorization": "Bearer <your-token>"},
)
response.raise_for_status()
with open("AAPL_2024.parquet", "wb") as f:
    f.write(response.content)`;

const TAM_FILE_SNIPPET = `from tam.marketdata.explorer_client import fetch_dataframe, download_csv

df = fetch_dataframe("AAPL", 2024)           # -> pandas DataFrame
download_csv("AAPL", 2024, "AAPL_2024.csv")  # -> saved straight to disk`;

const TAM_SQL_SNIPPET = `from tam.marketdata.explorer_client import connect

con = connect()
con.sql("SELECT * FROM daily_bars('AAPL') ORDER BY day").df()
con.sql("SELECT * FROM rolling_volatility('AAPL', 21) ORDER BY day").df()`;

const TAM_TOKEN_SNIPPET = `import os
os.environ["DATA_EXPLORER_TOKEN"] = "<your-token>"
# or: export DATA_EXPLORER_TOKEN=<your-token>  (before starting python)`;

const COLAB_SQL_SNIPPET = `from tam.marketdata.explorer_client import connect

# resolve_token() finds the DATA_EXPLORER_TOKEN Colab secret automatically --
# nothing else to configure, same code as running locally.
con = connect()
con.sql("SELECT * FROM daily_bars('AAPL') ORDER BY day").df()`;

type Tab = "curl" | "python" | "tam" | "colab";

const TABS: Array<{ key: Tab; label: string }> = [
  { key: "curl", label: "curl" },
  { key: "python", label: "Python" },
  { key: "tam", label: "tam" },
  { key: "colab", label: "Colab" },
];

export function ApiAccessPage() {
  const [tab, setTab] = useState<Tab>("curl");

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

      <div className="tabs">
        {TABS.map(({ key, label }) => (
          <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {tab === "curl" && (
        <>
          <p className="muted">Fetch a single file directly over plain HTTPS -- no SQL engine, no setup.</p>
          <CodeBlock language="bash" code={CURL_SNIPPET} />
        </>
      )}

      {tab === "python" && (
        <>
          <p className="muted">The same single-file download, with plain <code>requests</code> -- no <code>tam</code> dependency at all.</p>
          <CodeBlock language="python" code={PYTHON_SNIPPET} />
        </>
      )}

      {tab === "tam" && (
        <>
          <h2>One file, as a DataFrame</h2>
          <p className="muted">
            Same credential resolution order as <code>tam.discovery.auth.resolve_token()</code>: explicit kwarg
            &rarr; <code>DATA_EXPLORER_TOKEN</code> env var &rarr; Colab secret (auto-detected, see the Colab tab)
            &rarr; <code>~/.config/tam-data-explorer/token</code>.
          </p>
          <CodeBlock language="python" code={TAM_TOKEN_SNIPPET} />
          <CodeBlock language="python" code={TAM_FILE_SNIPPET} />

          <h2>Full SQL access (not just one file at a time)</h2>
          <p className="muted">
            <code>connect()</code> mints a short-lived, read-only R2 credential (scoped to just this bucket) behind
            the scenes and gives you a real SQL connection over the whole lake -- glob/multi-file queries included,
            without ever touching the real R2 account credentials. It refreshes itself automatically as the
            underlying credential approaches expiry, so a long notebook session keeps working.
          </p>
          <CodeBlock language="python" code={TAM_SQL_SNIPPET} />
        </>
      )}

      {tab === "colab" && (
        <>
          <p className="muted">
            In a Colab notebook, open the key-icon <strong>Secrets</strong> panel (left sidebar), add a secret named
            exactly <code>DATA_EXPLORER_TOKEN</code>, paste your token as its value, and toggle notebook access on.{" "}
            <code>resolve_token()</code> detects Colab automatically and reads it from there -- no code difference
            from running locally.
          </p>
          <CodeBlock language="python" code={COLAB_SQL_SNIPPET} />
        </>
      )}
    </div>
  );
}
