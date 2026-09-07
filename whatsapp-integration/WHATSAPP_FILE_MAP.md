# Where each file goes

Every path below is relative to the root of `cruzroja-agent` — the folder that
holds `package.json`, `turbo.json` and `apps/`.

The bundle mirrors that tree exactly, so the whole thing can be copied in one
command:

```bash
cp -r whatsapp-integration/files/. /path/to/cruzroja-agent/
```

Twenty-seven files. Fourteen are new, thirteen are edits to files that already
exist. Nothing is deleted, nothing is renamed, and no file outside this list is
touched.

Files are LF, matching what git has committed (the zip's working copy is CRLF
because it was checked out on Windows; git converts on checkout, so the diff
stays clean either way).

---

## 1. `packages/channels` — everything that knows Meta's shapes

This is the package the structure guide describes as "talking to WhatsApp and
the web". Four files were listed there and left empty; they are now filled in,
with tests.

| Path | | What it is |
|---|---|---|
| `packages/channels/src/whatsapp/parseWebhook.ts` | **new** | Reads Meta's webhook envelope. Separates real messages from delivery receipts, handles batches, and defines the queue job payload. |
| `packages/channels/src/whatsapp/client.ts` | **new** | The Graph API client. `sendText`, `sendTemplate`, `sendButtons`, `sendList`, `markRead`, with retries on 429/5xx only. |
| `packages/channels/src/whatsapp/format.ts` | **new** | Meta's hard limits. Splitting, clipping, and the interactive-payload budgets. Re-exports the presentation half from `packages/core`. |
| `packages/channels/src/whatsapp/window.ts` | **new** | The 24-hour customer service window. Used by the worker, and by the dashboard's countdown. |
| `packages/channels/src/whatsapp/__tests__/parseWebhook.test.ts` | **new** | 16 cases over real delivery shapes. |
| `packages/channels/src/whatsapp/__tests__/client.test.ts` | **new** | 14 cases with a scripted `fetch` — payload shapes, retry policy, Meta error codes. |
| `packages/channels/src/whatsapp/__tests__/format.test.ts` | **new** | 11 cases on limits and splitting. |
| `packages/channels/src/whatsapp/__tests__/window.test.ts` | **new** | 7 cases on the window arithmetic. |
| `packages/channels/vitest.config.ts` | **new** | Copied from `packages/core`, so `npm test` reaches this package. |
| `packages/channels/src/index.ts` | *edit* | Replaces the `TODO (WhatsApp channel)` block with the real exports. |
| `packages/channels/package.json` | *edit* | Adds `"test": "vitest run"`. No new dependencies — the client uses global `fetch`. |

**Why the client lives here and not in `packages/providers`:** providers are
adapters behind a `packages/core` interface, swappable for an AWS equivalent.
There is no AWS equivalent of WhatsApp. Meta is the channel, not a provider of
one, which is exactly the distinction `packages/channels` exists to draw.

---

## 2. `apps/api` — the webhook

| Path | | What it is |
|---|---|---|
| `apps/api/src/middleware/rawBody.ts` | **new** | `express.json({ verify })` that keeps the original bytes. |
| `apps/api/src/middleware/verifyMetaSignature.ts` | **new** | Constant-time HMAC-SHA256 check against `X-Hub-Signature-256`. |
| `apps/api/src/routes/whatsapp.ts` | **new** | `GET /webhooks/whatsapp` (handshake) and `POST /webhooks/whatsapp` (messages and receipts). |
| `apps/api/src/server.ts` | *edit* | Mounts the router **above** the global `express.json`, where the existing TODO says to. |
| `apps/api/src/container.ts` | *edit* | Adds `whatsapp: WhatsAppRuntime \| null`, built from config at boot. |
| `apps/api/src/express.d.ts` | *edit* | Declares `req.rawBody`. |

`apps/api/package.json` needs no change — it already depends on `@rag/channels`.

**Mount order is load-bearing.** The signature is an HMAC over the raw request
body. If the app-wide `express.json()` parses first, the bytes the signature was
computed over are gone, every real delivery is rejected as a forgery, and the
symptom looks exactly like a wrong app secret. The router is mounted above that
line and both files say so.

---

## 3. `apps/worker` — the reply

| Path | | What it is |
|---|---|---|
| `apps/worker/src/jobs/whatsappReply.ts` | **new** | The whole turn: duplicate check, read receipt, context, `answer()`, persist, send, record. Plus the per-number cost ceiling. |
| `apps/worker/src/index.ts` | *edit* | Replaces the throwing stub with the real handler; builds the `WhatsAppClient` and the LLM provider. |
| `apps/worker/package.json` | *edit* | Adds `@rag/channels`. |

---

## 4. `packages/shared` — config and one error

| Path | | What it is |
|---|---|---|
| `packages/shared/src/config.ts` | *edit* | Adds `getWhatsAppConfig()` and `isWhatsAppConfigured()`, the section the file's own TODO asked for. |
| `packages/shared/src/errors.ts` | *edit* | Adds `WebhookSignatureError` (403, `invalid_signature`). |
| `packages/shared/src/index.ts` | *edit* | Re-exports both. |

`config.ts` stays the only file in the repo that reads `process.env` — the
ESLint rule enforcing that is untouched and still passes.

---

## 5. `packages/providers` — one optional field

| Path | | What it is |
|---|---|---|
| `packages/providers/src/generation/persistTurn.ts` | *edit* | `PersistTurnInput` gains an optional `waMessageId`, written onto the user's `Message` inside the existing transaction. |

Nine added lines. The web path passes nothing and behaves exactly as before.
The field matters because `Message.waMessageId` is `@unique` — writing it inside
the transaction is what turns that constraint into a real idempotency guard
instead of a comment in the schema.

---

## 6. Root

| Path | | What it is |
|---|---|---|
| `scripts/checkpoint10-whatsapp.ts` | **new** | End-to-end proof: handshake, signature accepted, signature rejected, job consumed, redelivery ignored. Optionally sends one real message. |
| `package.json` | *edit* | Adds `"checkpoint:10"`. |
| `.env.example` | *edit* | Documents the four Meta secrets and seven tuning knobs. |

---

## What is deliberately not here

**The menu tree, the sign-up state machine and the lead push.** The Python
agent answers a tap from a `courses` table and walks the person through name →
email → phone → address, then POSTs the result to an external dashboard. This
monorepo answers the same questions a different way: retrieval over 104 cards,
prices verified against the database before the model sees them, and a
`RoutingEvent` row plus the built-in dashboard where the Python agent had an
HTTP push. Porting the state machine would mean two conversation engines
disagreeing about what a lead is.

What did carry over is everything underneath it: the raw-body signature check,
the batching and receipt-splitting in the parser, every Meta length limit, the
interactive send shapes, the per-number rate limit, and the phone-masked
logging. `client.sendButtons` / `client.sendList` and `format.clipSections` are
here and tested, so a menu is a new file in `packages/core`, not a rewrite of
the channel.

**Media download.** A photo or a voice note is acknowledged with a sentence
rather than fetched. `MessageAttachment` and `InboundMessage.mediaId` are both
ready for it; the missing piece is `apps/worker/src/jobs/mediaDownload.ts` and a
storage provider, which the structure guide already lists as its own job.

**Templates outside the 24-hour window.** `client.sendTemplate` and the
`WhatsAppTemplate` table exist and are tested. Nothing calls them yet, because
deciding *when* to re-engage someone is a product decision, not an integration
one.
