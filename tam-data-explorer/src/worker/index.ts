import { verifyAccess } from "./lib/access";
import { ApiError, jsonError } from "./lib/errors";
import { browse, listSymbols, listYears } from "./routes/browse";
import { exportFiles } from "./routes/export";
import { downloadCsv, downloadRaw, viewFile } from "./routes/file";
import type { Env } from "./types";

async function requireAccess(request: Request, env: Env): Promise<string> {
  const user = await verifyAccess(request, env);
  if (!user) throw new ApiError(401, "missing or invalid Access assertion");
  return user.identity;
}

async function handleApi(request: Request, env: Env, path: string[]): Promise<Response> {
  await requireAccess(request, env);
  const url = new URL(request.url);
  const method = request.method;

  if (path.length === 1 && path[0] === "browse" && method === "GET") {
    return Response.json(await browse(env, url.searchParams.get("prefix") ?? ""));
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
    return viewFile(env, key, page, pageSize);
  }
  if (path.length === 2 && path[0] === "file" && path[1] === "csv" && method === "GET") {
    const key = url.searchParams.get("key");
    if (!key) throw new ApiError(400, "key is required");
    return downloadCsv(env, key);
  }
  if (path.length === 1 && path[0] === "download" && method === "GET") {
    const key = url.searchParams.get("key");
    if (!key) throw new ApiError(400, "key is required");
    return downloadRaw(env, key);
  }
  if (path.length === 1 && path[0] === "export" && method === "GET") {
    const format = url.searchParams.get("format");
    if (format !== "parquet" && format !== "csv") throw new ApiError(400, "format must be 'parquet' or 'csv'");
    const prefixes = url.searchParams.getAll("prefix");
    const keys = url.searchParams.getAll("key");
    return exportFiles(env, prefixes, keys, format);
  }

  throw new ApiError(404, `no route for ${method} /api/${path.join("/")}`);
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
      if (segments[0] === "api") {
        return await handleApi(request, env, segments.slice(1));
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
