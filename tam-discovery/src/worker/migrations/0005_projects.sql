-- Projects: an optional, folder-like grouping for discoveries -- NOT an R2
-- key change (artifacts stay content-addressed/deduped globally, see
-- routes/publish.ts's createVersion()), purely a D1-level concept alongside
-- the existing type/tags grouping. `project_id` on discoveries is nullable
-- so every existing row keeps working unchanged, rendering under an
-- implicit "General" bucket -- not everything needs a project.
--
-- `archived_at` is soft-delete, same convention as discoveries.hidden_at
-- (migration 0003): archiving a project hides it from the active
-- picker/list without touching any discovery's own project_id -- a
-- discovery that already pointed at an archived project keeps pointing at
-- it and keeps showing that project's name, it just can't be newly
-- assigned to (or published into) anymore.

CREATE TABLE projects (
  id          TEXT PRIMARY KEY,
  slug        TEXT NOT NULL,
  name        TEXT NOT NULL,
  description TEXT,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  archived_at TEXT
);
CREATE UNIQUE INDEX idx_projects_slug ON projects(slug);
CREATE INDEX idx_projects_created_by ON projects(created_by);

ALTER TABLE discoveries ADD COLUMN project_id TEXT REFERENCES projects(id);
CREATE INDEX idx_discoveries_project_id ON discoveries(project_id);
