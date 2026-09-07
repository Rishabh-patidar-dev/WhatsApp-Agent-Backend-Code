import express from 'express';
import type { Request, RequestHandler } from 'express';

/**
 * Keep the exact bytes Meta sent.
 *
 * `express.json()` parses the body and throws the original text away. For every
 * other route that is exactly right. For the WhatsApp webhook it is fatal:
 * Meta's signature is an HMAC over the raw bytes, and `JSON.stringify` of the
 * parsed object is not those bytes. Key order is preserved by most engines,
 * but whitespace is not, `é` versus `é` is not, and a float that was sent
 * as `1.0` comes back as `1`. Any one of them changes the digest.
 *
 * The symptom is unmistakable once you know it and baffling until then: every
 * webhook is rejected as a forgery, the token and secret are demonstrably
 * correct, and Meta's dashboard shows the deliveries as failed.
 *
 * So this parser keeps a copy of the buffer as it goes past. `verify` is
 * express's own hook for it — it runs before parsing, with the raw body in
 * hand, on the same pass.
 *
 * ── Mount order ──────────────────────────────────────────────────────────
 *
 * This must be mounted on the webhook path *before* the app-wide
 * `express.json()`. Once the global parser has consumed the stream the body is
 * gone: a second parser sees an empty request and `req.rawBody` is undefined.
 * `server.ts` mounts it above the global parser and says so in a comment.
 */

export interface RawBodyOptions {
  /** Same shape as express's `limit`. Meta's batches are small; 1mb is generous. */
  limit?: string;
}

export function rawJsonBody({ limit = '1mb' }: RawBodyOptions = {}): RequestHandler {
  return express.json({
    limit,
    // Meta sends `application/json`. Kept explicit rather than `type: '*/*'`,
    // so a mis-configured sender is a 415 rather than a silent empty body.
    type: 'application/json',
    verify: (req, _res, buf) => {
      // `buf` is empty for a body-less request; storing it anyway keeps the
      // signature check's "missing body" and "empty body" cases distinct.
      (req as Request).rawBody = Buffer.from(buf);
    },
  });
}
