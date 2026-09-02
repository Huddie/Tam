import { verifyAccess } from "./lib/access";
import { verifyBearer } from "./lib/bearer";
import { ApiError, jsonError } from "./lib/errors";
import {
  createDiscovery,
  createVersion,
  finalizeVersion,
  getPublishedDiscovery,
  getPublishedVersions,
  listPublishedDiscoveries,
  whoami,
} from "./routes/publish";
import {
  assignProject,
  getDiscovery,
  getVersions,
  hideDiscovery,
  listDiscoveries,
  renameDiscovery,
  updateTags,
} from "./routes/discoveries";
import { archiveProject, createProject, listProjects, updateProject } from "./routes/projects";
import { listTags, listTypes } from "./routes/tags";
import { createToken, listTokens, revokeToken } from "./routes/tokens";
import { viewArtifact } from "./routes/view";
import type { Env } from "./types";

async function requireAccess(request: Request, env: Env): Promise<string> {
  const user = await verifyAccess(request, env);
  if (!user) throw new ApiError(401, "missing or invalid Access assertion");
  return user.email;
}

async function requireBearer(request: Request, env: Env): Promise<string> {
  const auth = await verifyBearer(request, env);
  if (!auth) throw new ApiError(401, "missing or invalid publishing token");
  return auth.user;
}

async function handlePublishApi(request: Request, env: Env, path: string[]): Promise<Response> {
  const user = await requireBearer(request, env);
  const method = request.method;

  // /api/publish/whoami
  if (path.length === 1 && path[0] === "whoami" && method === "GET") return whoami(user);

  // /api/publish/discoveries[...]
  if (path[0] === "discoveries") {
    if (path.length === 1 && method === "POST") return createDiscovery(request, env, user);
    if (path.length === 1 && method === "GET") return listPublishedDiscoveries(request, env);
    if (path.length === 2 && method === "GET") return getPublishedDiscovery(env, path[1]);
    if (path.length === 3 && path[2] === "versions" && method === "GET") return getPublishedVersions(env, path[1]);
    if (path.length === 3 && path[2] === "versions" && method === "POST") return createVersion(request, env, user, path[1]);
    if (path.length === 5 && path[2] === "versions" && path[4] === "finalize" && method === "POST") {
      return finalizeVersion(request, env, user, path[1], path[3]);
    }
  }

  throw new ApiError(404, `no publish route for ${method} /api/publish/${path.join("/")}`);
}

async function handleCatalogApi(request: Request, env: Env, path: string[]): Promise<Response> {
  const user = await requireAccess(request, env);
  const method = request.method;

  if (path.length === 1 && path[0] === "discoveries" && method === "GET") return listDiscoveries(request, env, user);
  if (path.length === 2 && path[0] === "discoveries" && method === "GET") return getDiscovery(env, path[1], user);
  if (path.length === 2 && path[0] === "discoveries" && method === "PATCH") return renameDiscovery(request, env, user, path[1]);
  if (path.length === 3 && path[0] === "discoveries" && path[2] === "versions" && method === "GET") {
    return getVersions(env, path[1]);
  }
  if (path.length === 3 && path[0] === "discoveries" && path[2] === "hide" && method === "POST") {
    return hideDiscovery(env, user, path[1]);
  }
  if (path.length === 3 && path[0] === "discoveries" && path[2] === "project" && method === "POST") {
    return assignProject(request, env, user, path[1]);
  }
  if (path.length === 3 && path[0] === "discoveries" && path[2] === "tags" && method === "POST") {
    return updateTags(request, env, user, path[1]);
  }
  if (path.length === 1 && path[0] === "tags" && method === "GET") return listTags(env);
  if (path.length === 1 && path[0] === "types" && method === "GET") return listTypes(env);

  if (path[0] === "projects") {
    if (path.length === 1 && method === "GET") return listProjects(env, user);
    if (path.length === 1 && method === "POST") return createProject(request, env, user);
    if (path.length === 2 && method === "PATCH") return updateProject(request, env, user, path[1]);
    if (path.length === 3 && path[2] === "archive" && method === "POST") return archiveProject(env, user, path[1]);
  }

  if (path[0] === "tokens") {
    if (path.length === 1 && method === "POST") return createToken(request, env, user);
    if (path.length === 1 && method === "GET") return listTokens(env, user);
    if (path.length === 2 && method === "DELETE") return revokeToken(env, user, path[1]);
  }

  throw new ApiError(404, `no catalog route for ${method} /api/${path.join("/")}`);
}

async function handleView(request: Request, env: Env, idOrSlug: string): Promise<Response> {
  await requireAccess(request, env);
  return viewArtifact(env, idOrSlug);
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
      if (segments[0] === "api" && segments[1] === "publish") {
        return await handlePublishApi(request, env, segments.slice(2));
      }
      if (segments[0] === "api") {
        return await handleCatalogApi(request, env, segments.slice(1));
      }
      if (segments[0] === "d" && segments.length === 3 && segments[2] === "view") {
        return await handleView(request, env, segments[1]);
      }
      if (segments[0] === "d" && segments.length === 2) {
        return await serveSpaShell(request, env);
      }
      if (segments.length === 0 || segments[0] === "settings") {
        return await serveSpaShell(request, env);
      }

      // Anything else reaching the Worker didn't match a static asset either
      // (Cloudflare serves matching files under `assets.directory` before
      // the Worker ever runs) -- fall back to the SPA shell so client-side
      // routing (react-router) can 404 within the app instead of at the edge.
      return await serveSpaShell(request, env);
    } catch (error) {
      if (error instanceof ApiError) return jsonError(error.status, error.message);
      console.error(error);
      return jsonError(500, "internal error");
    }
  },
};
