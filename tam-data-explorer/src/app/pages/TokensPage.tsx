import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { type TokenSummary, createToken, listTokens, revokeToken } from "../api";
import { useSort } from "../useSort";

export function TokensPage() {
  const [tokens, setTokens] = useState<TokenSummary[]>([]);
  const [newName, setNewName] = useState("");
  const [freshToken, setFreshToken] = useState<{ name: string; token: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    listTokens()
      .then((r) => setTokens(r.tokens))
      .catch((e) => setError(String(e)));
  }

  useEffect(refresh, []);

  async function handleCreate() {
    const name = newName.trim();
    if (!name) {
      setError("Give the token a name first (e.g. \"colab\", \"laptop\").");
      return;
    }
    try {
      const result = await createToken(name);
      setFreshToken({ name: result.name, token: result.token });
      setNewName("");
      setError(null);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleRevoke(id: string) {
    try {
      await revokeToken(id);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  const { sorted, toggleSort, indicator } = useSort<TokenSummary>(tokens, (token, key) => {
    switch (key) {
      case "name":
        return token.name.toLowerCase();
      case "created":
        return token.created_at;
      case "lastUsed":
        return token.last_used_at ?? "";
      case "status":
        return token.revoked_at ? "revoked" : "active";
      default:
        return "";
    }
  });

  return (
    <div className="page page-wide">
      <Link className="back-link" to="/">
        &larr; Back to browse
      </Link>
      <h1>Personal tokens</h1>
      <p className="muted">
        A personal token authenticates scripts, notebooks, and SQL access without an interactive login -- see{" "}
        <Link to="/api-access">API access</Link> for exact usage.
      </p>
      <ul className="muted">
        <li>Query data or run SQL here, from a script or notebook.</li>
        <li>
          The same token also works on <a href="https://discovery.tamquant.com/settings/tokens">Discovery</a> for
          publishing -- one token, not two.
        </li>
      </ul>
      <p className="callout">
        Treat it like a password: whoever has it can query or publish on your behalf until you revoke it below.
        Each token is yours alone -- revoking one never affects anyone else's access.
      </p>

      <div className="toolbar">
        <input
          placeholder="Name this token (e.g. colab, laptop) -- must be unique"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
        />
        <button onClick={handleCreate}>Create</button>
      </div>

      {freshToken && (
        <p className="callout">
          <strong>"{freshToken.name}" -- copy this now, it won't be shown again:</strong>
          <br />
          <code>{freshToken.token}</code>
        </p>
      )}

      {error && <p className="error">{error}</p>}

      <div className="table-wrap" style={{ marginTop: "1.25rem" }}>
      <table>
        <thead>
          <tr>
            <th className="sortable" onClick={() => toggleSort("name")}>
              Name{indicator("name")}
            </th>
            <th className="sortable" onClick={() => toggleSort("created")}>
              Created{indicator("created")}
            </th>
            <th className="sortable" onClick={() => toggleSort("lastUsed")}>
              Last used{indicator("lastUsed")}
            </th>
            <th className="sortable" onClick={() => toggleSort("status")}>
              Status{indicator("status")}
            </th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sorted.map((token) => (
            <tr key={token.id}>
              <td className="mono">{token.name}</td>
              <td className="muted mono">{new Date(token.created_at).toLocaleString()}</td>
              <td className="muted mono">{token.last_used_at ? new Date(token.last_used_at).toLocaleString() : "never"}</td>
              <td>{token.revoked_at ? "revoked" : "active"}</td>
              <td>{!token.revoked_at && <button onClick={() => handleRevoke(token.id)}>Revoke</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}
