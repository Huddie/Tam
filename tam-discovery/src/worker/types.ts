export interface Env {
  DB: D1Database;
  ARTIFACTS: R2Bucket;
  ASSETS: Fetcher;
  R2_S3_ENDPOINT: string;
  R2_BUCKET_NAME: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  TOKEN_HMAC_SECRET: string;
  R2_ACCESS_KEY_ID: string;
  R2_SECRET_ACCESS_KEY: string;
}

export interface AuthedUser {
  email: string;
}

export interface DiscoveryRow {
  id: string;
  slug: string | null;
  type: string;
  title: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  latest_version_id: string;
}

export interface VersionRow {
  id: string;
  discovery_id: string;
  version_number: number;
  status: "pending" | "finalized";
  title: string;
  description: string | null;
  uploaded_by: string;
  created_at: string;
  source_file: string | null;
  git_commit: string | null;
  git_branch: string | null;
  git_repo: string | null;
  git_dirty: number | null;
  generated_at: string | null;
  r2_key: string;
  size_bytes: number | null;
  metadata_json: string | null;
}
