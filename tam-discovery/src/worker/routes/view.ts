import { resolveVersionTarget } from "../lib/d1";
import { ApiError } from "../lib/errors";
import type { Env } from "../types";

/** GET /d/:idOrSlug/view -- streams the artifact straight from the private
 * R2 bucket (the Worker binding is the ONLY reader; there is no public
 * r2.dev URL or custom domain on the bucket). The `sandbox` CSP directive
 * forces an opaque, unique origin for this response regardless of hostname
 * or how it's embedded -- combined with the SPA's `<iframe sandbox
 *="allow-scripts">` (no `allow-same-origin`), this is what actually
 * neutralizes untrusted artifact JS as an XSS vector against the
 * authenticated app's own session (cookies/localStorage/same-origin fetch
 * are all unreachable from an opaque origin), not just an embedding nicety. */
export async function viewArtifact(env: Env, idOrSlug: string): Promise<Response> {
  const target = await resolveVersionTarget(env, idOrSlug);
  if (!target) throw new ApiError(404, `no discovery/version ${idOrSlug}`);

  const object = await env.ARTIFACTS.get(target.version.r2_key);
  if (!object) throw new ApiError(404, "artifact bytes are missing from storage");

  return new Response(object.body, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy":
        "sandbox allow-scripts; default-src 'none'; script-src https://cdn.plot.ly 'unsafe-inline'; style-src 'unsafe-inline'",
      "X-Content-Type-Options": "nosniff",
      "Cache-Control": "private, max-age=0, must-revalidate",
    },
  });
}
