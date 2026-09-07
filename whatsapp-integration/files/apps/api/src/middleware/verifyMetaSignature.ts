import { createHmac, timingSafeEqual } from 'node:crypto';
import type { RequestHandler } from 'express';
import { WebhookSignatureError } from '@rag/shared';

/**
 * Proving a webhook really came from Meta.
 *
 * The URL is public and its shape is documented, so anyone who finds it can
 * POST a message that looks like a customer's. Without this middleware that
 * message would be answered, billed, and written into the conversation history
 * of a real contact — and, if it asked for a price, would put the client's
 * commercial terms wherever the sender wanted them.
 *
 * Meta signs every delivery with an HMAC-SHA256 over the raw body, keyed on the
 * app secret, in an `X-Hub-Signature-256: sha256=<hex>` header. Checking it is
 * what separates a customer from a stranger with curl.
 *
 * ── Three details that are each a whole bug on their own ─────────────────
 *
 *   The digest is over the **raw bytes** — see `rawBody.ts`. Re-serialising the
 *   parsed JSON produces a different digest and rejects every real delivery.
 *
 *   The comparison is **constant-time**. A byte-by-byte `===` leaks, through
 *   timing, how much of a guessed signature was right, which is enough to
 *   forge one given patience and a fast network.
 *
 *   A missing app secret is **fatal, not permissive**. The tempting version of
 *   this file logs a warning and passes the request through when the secret is
 *   absent, so local development is easy. That warning then scrolls past in
 *   production and the endpoint is spoofable by anyone who learns the URL.
 *   `getWhatsAppConfig()` requires the secret, and the checkpoint script signs
 *   its own test payloads, so the convenient version has no reason to exist.
 */

export const SIGNATURE_HEADER = 'x-hub-signature-256';
const PREFIX = 'sha256=';

export function verifyMetaSignature(appSecret: string): RequestHandler {
  const secret = Buffer.from(appSecret, 'utf8');

  return (req, _res, next) => {
    const raw = req.rawBody;
    if (!raw) {
      // Not a signature failure — a wiring failure. The raw-body parser did
      // not run on this path, which means the mount order in server.ts is
      // wrong. Said plainly, because the alternative is hours spent
      // regenerating a correct app secret.
      next(
        new WebhookSignatureError(
          'No raw body on the request — rawJsonBody() must be mounted on this route before express.json()',
        ),
      );
      return;
    }

    const header = req.get(SIGNATURE_HEADER);
    if (!header || !header.startsWith(PREFIX)) {
      next(new WebhookSignatureError(`Missing or malformed ${SIGNATURE_HEADER} header`));
      return;
    }

    const expected = createHmac('sha256', secret).update(raw).digest();

    // `Buffer.from(…, 'hex')` does not throw on rubbish — it stops at the first
    // invalid pair and returns a short buffer. That is handled by the length
    // check below rather than by a parse guard that would never fire.
    const received = Buffer.from(header.slice(PREFIX.length).trim(), 'hex');

    // timingSafeEqual throws on a length mismatch rather than returning false,
    // and a truncated signature is a perfectly ordinary thing for an attacker
    // to send.
    if (received.length !== expected.length || !timingSafeEqual(received, expected)) {
      next(new WebhookSignatureError('Webhook signature did not match the app secret'));
      return;
    }

    next();
  };
}
