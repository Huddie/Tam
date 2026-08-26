-- discovery_tags is a cache (current tags, fast catalog filtering); version_tags
-- is the permanent per-version record -- immutability requires a version's tag
-- set AT PUBLISH TIME to stay inspectable even after the discovery's current
-- tags change. IDs are UUIDv4 everywhere (never autoincrement) so URLs never
-- leak a sequential total-items count.

CREATE TABLE discoveries (
  id                TEXT PRIMARY KEY,
  slug              TEXT,
  type              TEXT NOT NULL DEFAULT 'dashboard',
  title             TEXT NOT NULL,
  created_by        TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  latest_version_id TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_discoveries_slug ON discoveries(slug) WHERE slug IS NOT NULL;
CREATE INDEX idx_discoveries_type ON discoveries(type);
CREATE INDEX idx_discoveries_updated_at ON discoveries(updated_at DESC);
CREATE INDEX idx_discoveries_created_at ON discoveries(created_at DESC);
CREATE INDEX idx_discoveries_created_by ON discoveries(created_by);

CREATE TABLE discovery_versions (
  id             TEXT PRIMARY KEY,
  discovery_id   TEXT NOT NULL,
  version_number INTEGER NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending',
  title          TEXT NOT NULL,
  description    TEXT,
  uploaded_by    TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  source_file    TEXT,
  git_commit     TEXT,
  git_branch     TEXT,
  git_repo       TEXT,
  git_dirty      INTEGER,
  generated_at   TEXT,
  r2_key         TEXT NOT NULL,
  size_bytes     INTEGER,
  metadata_json  TEXT,
  UNIQUE (discovery_id, version_number)
);
CREATE INDEX idx_discovery_versions_discovery_id ON discovery_versions(discovery_id);

CREATE TABLE tags (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE version_tags (
  version_id TEXT NOT NULL,
  tag_id     INTEGER NOT NULL,
  PRIMARY KEY (version_id, tag_id)
);
CREATE INDEX idx_version_tags_tag_id ON version_tags(tag_id);

CREATE TABLE discovery_tags (
  discovery_id TEXT NOT NULL,
  tag_id       INTEGER NOT NULL,
  PRIMARY KEY (discovery_id, tag_id)
);
CREATE INDEX idx_discovery_tags_tag_id ON discovery_tags(tag_id);

CREATE TABLE tokens (
  id           TEXT PRIMARY KEY,
  user         TEXT NOT NULL,
  token_hash   TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at   TEXT
);
CREATE UNIQUE INDEX idx_tokens_hash ON tokens(token_hash);
CREATE INDEX idx_tokens_user ON tokens(user);
