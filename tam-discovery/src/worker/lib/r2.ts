import { AwsClient } from "aws4fetch";
import type { Env } from "../types";

// 10 minutes -- long enough for a CLI/notebook to start the PUT right after
// create_version() responds, short enough that a leaked URL (e.g. in a shell
// history) isn't useful for long.
const PRESIGN_TTL_SECONDS = 600;

/** A short-TTL, single-object-scoped presigned PUT URL for `key`, signed
 * Worker-side from the R2 API token stored as R2_ACCESS_KEY_ID/
 * R2_SECRET_ACCESS_KEY secrets -- those credentials never leave the Worker;
 * the CLI/SDK only ever sees the resulting URL. */
export async function createPresignedUploadUrl(env: Env, key: string): Promise<string> {
  const client = new AwsClient({
    accessKeyId: env.R2_ACCESS_KEY_ID,
    secretAccessKey: env.R2_SECRET_ACCESS_KEY,
    service: "s3",
    region: "auto",
  });

  const endpoint = new URL(env.R2_S3_ENDPOINT);
  const objectUrl = new URL(`${endpoint.origin}/${env.R2_BUCKET_NAME}/${key}`);
  objectUrl.searchParams.set("X-Amz-Expires", String(PRESIGN_TTL_SECONDS));

  const signed = await client.sign(objectUrl.toString(), {
    method: "PUT",
    aws: { signQuery: true },
  });
  return signed.url;
}
