import path from "node:path";
import { defineWorkersConfig, readD1Migrations } from "@cloudflare/vitest-pool-workers/config";

const migrationsPath = path.join(__dirname, "src/worker/migrations");
const migrations = await readD1Migrations(migrationsPath);

export default defineWorkersConfig({
  test: {
    setupFiles: ["./test/apply-migrations.ts"],
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.jsonc" },
        miniflare: {
          // Test-only bindings -- readD1Migrations()'s result, consumed by
          // test/apply-migrations.ts, plus fake (never-real) secrets so
          // requireBearer/requireAccess have something deterministic to
          // check against without ever touching `wrangler secret put`
          // values or a real Cloudflare account.
          bindings: {
            TEST_MIGRATIONS: migrations,
            ACCESS_TEAM_DOMAIN: "test-team.cloudflareaccess.com",
            ACCESS_AUD: "test-aud",
            TOKEN_HMAC_SECRET: "test-hmac-secret",
            R2_ACCESS_KEY_ID: "test-key-id",
            R2_SECRET_ACCESS_KEY: "test-secret-key",
          },
        },
      },
    },
  },
});
