import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { type TokenSummary, createToken, listTokens, revokeToken } from "../api";

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

  return (
    <div className="page page-narrow">
      <Link className="back-link" to="/">
        &larr; Back to catalog
      </Link>
      <h1>Publishing tokens</h1>
      <p className="muted">
        Used by <code>upload-discovery login</code> (or <code>tam.discovery.upload(token=...)</code>) to publish from
        a shell, notebook, or Colab -- see the README for setup.
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
          <strong>
            "{freshToken.name}" -- copy this now, it won't be shown again:
          </strong>
          <br />
          <code>{freshToken.token}</code>
        </p>
      )}

      {error && <p className="error">{error}</p>}

      <table style={{ marginTop: "1.25rem" }}>
        <thead>
          <tr>
            <th>Name</th>
            <th>Created</th>
            <th>Last used</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {tokens.map((token) => (
            <tr key={token.id}>
              <td>{token.name}</td>
              <td className="muted">{new Date(token.created_at).toLocaleString()}</td>
              <td className="muted">{token.last_used_at ? new Date(token.last_used_at).toLocaleString() : "never"}</td>
              <td>{token.revoked_at ? "revoked" : "active"}</td>
              <td>{!token.revoked_at && <button onClick={() => handleRevoke(token.id)}>Revoke</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
