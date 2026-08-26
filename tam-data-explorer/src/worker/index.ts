import { verifyAccess } from "./lib/access";
import { verifyBearer } from "./lib/bearer";
import { ApiError, jsonError } from "./lib/errors";
import { browse, listSymbols, listYears } from "./routes/browse";
import { issueCredentials } from "./routes/credentials";
import { exportFiles } from "./routes/export";
import { downloadCsv, downloadRaw, viewFile, viewFileDates } from "./routes/file";
import { createToken, listTokens, revokeToken } from "./routes/tokens";
import type { Env } from "./types";

async function requireAccess(request: Request, env: Env): Promise<string> {
  const user = await verifyAccess(request, env);
  if (!user) throw new ApiError(401, "missing or invalid Access assertion");
  return user.identity;
}

async function requireBearer(request: Request, env: Env): Promise<string> {
  const auth = await verifyBearer(request, env);
  if (!auth) throw new ApiError(401, "missing or invalid personal API token");
  return auth.user;
}

/** The read-only data routes (browse/symbols/years/file/export) -- reachable
 * BOTH as /api/* (Access-gated, used by the browser SPA) and /api/token/*
 * (bearer-gated -- see README's runbook for the matching Access "Bypass"
 * Application that excludes this prefix from the edge login redirect --
 * used by scripts/notebooks/DuckDB). Identical logic either way; only how
 * the caller got authenticated differs, already resolved by the two
 * callers below before this ever runs. */
async function handleDataRoutes(request: Request, env: Env, path: string[]): Promise<Response> {
  const url = new URL(request.url);
  const method = request.method;

  if (path.length === 1 && path[0] === "browse" && method === "GET") {
    return Response.json(await browse(env, url.searchParams.get("prefix") ?? "", url.searchParams.get("cursor") ?? undefined));
  }
  if (path.length === 1 && path[0] === "symbols" && method === "GET") {
    return Response.json({ symbols: await listSymbols(env) });
  }
  if (path.length === 3 && path[0] === "symbols" && path[2] === "years" && method === "GET") {
    return Response.json({ years: await listYears(env, path[1]) });
  }
  if (path.length === 1 && path[0] === "file" && method === "GET") {
    const key = url.searchParams.get("key");
    if (!key) throw new ApiError(400, "key is required");
    const page = Number(url.searchParams.get("page") ?? "1");
    const pageSize = Number(url.searchParams.get("pageSize") ?? "0");
    const monthParam = url.searchParams.get("month");
    const dayParam = url.searchParams.get("day");
    return viewFile(
      env,
      key,
      page,
      pageSize,
      monthParam ? Number(monthParam) : undefined,
      dayParam ? Number(dayParam) : undefined,
      url.searchParams.get("start") ?? undefined,
      url.searchParams.get("end") ?? undefined,
    );
  }
  if (path.length === 2 && path[0] === "file" && path[1] === "dates" && method === "GET") {
    const key = url.searchParams.get("key");
    if (!key) throw new ApiError(400, "key is required");
    return viewFileDates(env, key);
  }
  if (path.length === 2 && path[0] === "file" && path[1] === "csv" && method === "GET") {
    const key = url.searchParams.get("key");
    if (!key) throw new ApiError(400, "key is required");
    return downloadCsv(env, key);
  }
  if (path.length === 1 && path[0] === "download" && method === "GET") {
    const key = url.searchParams.get("key");
    if (!key) throw new ApiError(400, "key is required");
    return downloadRaw(env, key, request.headers.get("Range"));
  }
  if (path.length === 1 && path[0] === "export" && method === "GET") {
    const format = url.searchParams.get("format");
    if (format !== "parquet" && format !== "csv") throw new ApiError(400, "format must be 'parquet' or 'csv'");
    const prefixes = url.searchParams.getAll("prefix");
    const keys = url.searchParams.getAll("key");
    return exportFiles(env, prefixes, keys, format);
  }

  throw new ApiError(404, `no route for ${method} /${path.join("/")}`);
}

/** /api/tokens[/...] -- self-service personal-token management, ACCESS-gated
 * (only a real, logged-in GitHub user can create/list/revoke THEIR OWN
 * tokens; there's no bearer-token path to this route on purpose). */
async function handleTokensApi(request: Request, env: Env, path: string[]): Promise<Response> {
  const user = await requireAccess(request, env);
  const method = request.method;

  if (path.length === 0 && method === "POST") return createToken(request, env, user);
  if (path.length === 0 && method === "GET") return listTokens(env, user);
  if (path.length === 1 && method === "DELETE") return revokeToken(env, user, path[0]);

  throw new ApiError(404, `no token route for ${method} /api/tokens/${path.join("/")}`);
}

async function serveSpaShell(request: Request, env: Env): Promise<Response> {
  await requireAccess(request, env);
  const indexRequest = new Request(new URL("/index.html", request.url), request);
  return env.ASSETS.fetch(indexRequest);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const segments = url.pathname.split("/").filter(Boolean);

    try {
      if (segments[0] === "api" && segments[1] === "tokens") {
        return await handleTokensApi(request, env, segments.slice(2));
      }
      if (segments[0] === "api" && segments[1] === "token") {
        await requireBearer(request, env);
        const path = segments.slice(2);
        if (path.length === 1 && path[0] === "credentials" && request.method === "POST") {
          return await issueCredentials(request, env);
        }
        return await handleDataRoutes(request, env, path);
      }
      if (segments[0] === "api") {
        await requireAccess(request, env);
        return await handleDataRoutes(request, env, segments.slice(1));
      }
      // Anything else reaching the Worker didn't match a static asset
      // either (Cloudflare serves those directly, before the Worker ever
      // runs) -- fall back to the SPA shell for client-side routing.
      return await serveSpaShell(request, env);
    } catch (error) {
      if (error instanceof ApiError) return jsonError(error.status, error.message);
      console.error(error);
      return jsonError(500, "internal error");
    }
  },
};
