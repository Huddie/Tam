-- Soft-delete for discoveries -- hidden_at marks a discovery as removed from
-- the catalog listing/search WITHOUT actually deleting its rows or R2
-- artifact bytes, so a URL someone already has (permalink or version URL)
-- keeps resolving. Only listDiscoveries() filters on this; getDiscovery(),
-- getVersions(), and the /d/:id/view route deliberately don't -- direct
-- links to a hidden discovery still work, only its catalog visibility
-- changes. NULL (the default) means visible/never hidden.

ALTER TABLE discoveries ADD COLUMN hidden_at TEXT;
