import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { type Project, archiveProject, createProject, listProjects, updateProject } from "../api";
import { useSort } from "../useSort";

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");

  function refresh() {
    listProjects()
      .then((r) => setProjects(r.projects))
      .catch((e) => setError(String(e)));
  }

  useEffect(refresh, []);

  async function handleCreate() {
    const name = newName.trim();
    if (!name) {
      setError("Give the project a name first (e.g. \"Q3 Earnings\").");
      return;
    }
    try {
      await createProject(name, newDescription.trim() || undefined);
      setNewName("");
      setNewDescription("");
      setError(null);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  function startEdit(project: Project) {
    setEditingId(project.id);
    setEditName(project.name);
    setEditDescription(project.description ?? "");
  }

  async function saveEdit() {
    if (!editingId || !editName.trim()) return;
    try {
      await updateProject(editingId, { name: editName.trim(), description: editDescription.trim() });
      setEditingId(null);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleArchive(id: string) {
    // Soft-archive (see routes/projects.ts's archiveProject()) -- reversible
    // in principle, but there's no "restore" UI yet, so a plain confirm()
    // is the only "are you sure" this gets. Discoveries already assigned
    // keep pointing at it and keep showing its name; it just leaves this
    // active list and the assignment pickers.
    if (!window.confirm("Delete this project? Discoveries already in it will keep showing it, but it won't be assignable anymore.")) {
      return;
    }
    try {
      await archiveProject(id);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  const { sorted, toggleSort, indicator } = useSort<Project>(projects, (project, key) => {
    switch (key) {
      case "name":
        return project.name.toLowerCase();
      case "creator":
        return project.created_by.toLowerCase();
      case "updated":
        return project.updated_at;
      case "count":
        return project.discovery_count;
      default:
        return "";
    }
  });

  return (
    <div className="page page-wide">
      <Link className="back-link" to="/">
        &larr; Back to catalog
      </Link>
      <h1>Projects</h1>
      <p className="muted">
        An optional, folder-like grouping for discoveries -- publish into one with{" "}
        <code>upload-discovery ... --project &lt;slug&gt;</code> or <code>upload(..., project="&lt;slug&gt;")</code>.
        Discoveries not assigned to any project show up under "General" in the catalog.
      </p>

      <div className="toolbar">
        <input placeholder="Project name (e.g. Q3 Earnings)" value={newName} onChange={(e) => setNewName(e.target.value)} />
        <input
          placeholder="What's the goal? (optional)"
          value={newDescription}
          onChange={(e) => setNewDescription(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
        />
        <button onClick={handleCreate}>Create</button>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="table-wrap" style={{ marginTop: "1.25rem" }}>
        <table>
          <thead>
            <tr>
              <th className="sortable" onClick={() => toggleSort("name")}>
                Name{indicator("name")}
              </th>
              <th>Description</th>
              <th className="sortable" onClick={() => toggleSort("count")}>
                Discoveries{indicator("count")}
              </th>
              <th className="sortable" onClick={() => toggleSort("creator")}>
                Creator{indicator("creator")}
              </th>
              <th className="sortable" onClick={() => toggleSort("updated")}>
                Updated{indicator("updated")}
              </th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sorted.map((project) => (
              <tr key={project.id}>
                {editingId === project.id ? (
                  <>
                    <td>
                      <input value={editName} onChange={(e) => setEditName(e.target.value)} autoFocus />
                    </td>
                    <td>
                      <input value={editDescription} onChange={(e) => setEditDescription(e.target.value)} />
                    </td>
                    <td className="muted">{project.discovery_count}</td>
                    <td className="muted">{project.created_by}</td>
                    <td className="muted mono">{new Date(project.updated_at).toLocaleDateString()}</td>
                    <td>
                      <button className="primary" disabled={!editName.trim()} onClick={saveEdit}>
                        Save
                      </button>
                      <button className="secondary" onClick={() => setEditingId(null)}>
                        Cancel
                      </button>
                    </td>
                  </>
                ) : (
                  <>
                    <td>
                      <Link to={`/?project=${encodeURIComponent(project.slug)}`}>{project.name}</Link>
                    </td>
                    <td className="muted">{project.description || <span className="muted">(no description)</span>}</td>
                    <td className="muted">{project.discovery_count}</td>
                    <td className="muted">{project.created_by}</td>
                    <td className="muted mono" title={new Date(project.updated_at).toLocaleString()}>
                      {new Date(project.updated_at).toLocaleDateString()}
                    </td>
                    <td>
                      {project.can_manage && (
                        <>
                          <button onClick={() => startEdit(project)}>Edit</button>
                          <button className="danger" onClick={() => handleArchive(project.id)}>
                            Delete
                          </button>
                        </>
                      )}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {projects.length === 0 && !error && <p className="muted">No projects yet -- everything shows up under "General".</p>}
    </div>
  );
}
