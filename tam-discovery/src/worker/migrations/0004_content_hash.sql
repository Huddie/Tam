-- Content-hash-based dedup for published artifacts. content_hash (sha256
-- hex of the uploaded HTML bytes, computed client-side) lets createVersion()
-- recognize two cases without ever re-uploading identical bytes:
--   1. This exact discovery already has a finalized version with this exact
--      hash -- a pure re-publish of unchanged content, nothing to do at all.
--   2. Some OTHER version (any discovery) already has this hash -- the R2
--      object already exists at artifacts/{hash}.html, so this new version
--      can point at it directly and finalize immediately, skipping the
--      upload step (still a genuinely new version row/version_number for
--      THIS discovery, just backed by shared bytes).
-- NULL only for rows that predate this migration.

ALTER TABLE discovery_versions ADD COLUMN content_hash TEXT;
CREATE INDEX idx_discovery_versions_content_hash ON discovery_versions(content_hash);
