# Connecting the agent to WhatsApp

Start to finish: a Meta app, four secrets, one `cp -r`, and a checkpoint script
that proves it works before a real customer ever writes in.

Roughly 45 minutes the first time, most of it waiting on Meta's dashboard.

---

## Part A — Meta, once

You need a Meta account with a Business portfolio. Everything below is free;
the WhatsApp test number Meta issues sends to five verified recipients, which is
plenty to get this working.

### A1. Create the app

1. <https://developers.facebook.com/apps> → **Create app**
2. Use case: **Other** → type: **Business**
3. Attach it to your Business portfolio
4. On the app's dashboard, find **WhatsApp** → **Set up**

### A2. The phone number id

**WhatsApp → API Setup.** Meta issues a test number and shows you:

- **Phone number ID** — a long number. This is `WHATSAPP_PHONE_NUMBER_ID`.
  It is *not* the phone number itself; that is a common and confusing mix-up,
  because both are shown on the same line.
- A temporary access token — **do not use this one.** It expires in 24 hours,
  and the failure that follows looks like a code bug: sends stop working and
  nothing else changes.

Add your own phone under **To** → *Manage phone number list* so you can test.

### A3. A permanent access token

The 24-hour token is the single most common reason an integration "stops
working on Monday".

1. <https://business.facebook.com/settings> → **Users → System users**
2. **Add** → name it (`cruzroja-agent`), role **Admin**
3. **Add assets** → your app → toggle **Manage app**
4. **Generate new token** → pick the app → permissions:
   `whatsapp_business_messaging` and `whatsapp_business_management`
5. Expiration: **Never**
6. Copy it. This is `WHATSAPP_ACCESS_TOKEN` — Meta shows it exactly once.

### A4. The app secret

**App dashboard → Settings → Basic → App secret → Show.**
This is `WHATSAPP_APP_SECRET`.

It signs every webhook delivery. Without it the endpoint cannot tell a real
customer from anyone who has found the URL, so `getWhatsAppConfig()` requires
it. The checkpoint script signs its own test payloads, so there is no reason to
want a bypass.

### A5. Invent a verify token

Any string. `openssl rand -hex 16` is fine. This is `WHATSAPP_VERIFY_TOKEN` —
Meta echoes it back once, during the subscription handshake in Part C.

---

## Part B — The repository

### B1. Copy the bundle in

From the folder that holds this file:

```bash
cp -r files/. /path/to/cruzroja-agent/
```

Twenty-seven files: fourteen new, thirteen replacing existing ones. Nothing is
deleted or renamed. To review the thirteen edits before applying them, read
`patches/whatsapp-integration.diff` — 605 lines, and applying the bundle
produces exactly that diff.

### B2. Fill in `.env`

```bash
cd /path/to/cruzroja-agent
cp .env.example .env    # if you have not already
```

The four required values, plus the version pin:

```dotenv
WHATSAPP_PHONE_NUMBER_ID=106540352242922
WHATSAPP_ACCESS_TOKEN=EAAG...            # the permanent one from A3
WHATSAPP_VERIFY_TOKEN=whatever-you-chose # from A5
WHATSAPP_APP_SECRET=a1b2c3...            # from A4
WHATSAPP_API_VERSION=v21.0
```

Leave all four blank and the channel is simply off: the webhook is not mounted
and the worker logs that it is disabled. Set *one* of them and all four are
required — a half-configured channel would serve an endpoint that cannot verify
a signature, so it fails at boot instead.

The seven tuning knobs below them all have defaults; `.env.example` explains
each. `WHATSAPP_WORKER_CONCURRENCY` is the one worth a thought: above 1, two
messages sent seconds apart by the same person can be answered out of order.

### B3. Install and check

```bash
npm install                              # picks up @rag/channels in the worker
npm run db:generate                      # if you have not run it in this checkout
npm run typecheck
npm test -w @rag/channels                # 48 cases over the four channel files
npm run lint
```

`npm test -w @rag/channels` is new — the package had no test script before.

---

## Part C — Point Meta at your machine

### C1. Run both processes

```bash
npm run dev:api        # terminal 1 — :3001
npm run dev:worker     # terminal 2
```

The worker's startup line should read `whatsapp: 'enabled'`. If it says
`disabled`, `.env` is not being read — the file must be at the repo root, which
is where `packages/shared/src/config.ts` looks.

### C2. Expose the API

```bash
ngrok http 3001        # terminal 3
```

Copy the `https://` URL. Also put it in `.env` as `API_PUBLIC_ORIGIN` so the
checkpoint script knows where to send its test payloads, then restart the API.

### C3. Subscribe the webhook

**App dashboard → WhatsApp → Configuration → Edit.**

| Field | Value |
|---|---|
| Callback URL | `https://<your-ngrok>.ngrok-free.app/webhooks/whatsapp` |
| Verify token | the string from A5 |

**Verify and save.** Meta immediately issues a `GET` with `hub.challenge`; the
route echoes it back as plain text. If it fails, the API log has the reason —
almost always a mismatched verify token or a stale ngrok URL.

Then, still on that page, **Manage** the webhook fields and subscribe to:

- **messages** — required; this is inbound traffic *and* delivery receipts

Nothing else is needed. Subscribing to extra fields is harmless — the route
ignores any change whose `field` is not `messages` — but it is noise.

### C4. Prove it works

```bash
npm run checkpoint:10
```

Five assertions, none of which touch Meta:

1. the handshake echoes `hub.challenge`, and a wrong token is refused
2. a correctly signed POST is accepted — the raw body survived the parser
3. a tampered POST and an unsigned POST are both rejected with 403
4. the message became a job, the worker consumed it, and an answer is in the
   database — the script prints it
5. a redelivery of the same message id writes no new rows

Point 3 is the one that earns its keep. A signature check that always passes
looks identical to one that works, right up until somebody posts a fabricated
customer message at a public URL.

To also send a real message:

```bash
npm run checkpoint:10 -- --send=5215598765432
```

That number must have written to your test number in the last 24 hours, or Meta
refuses with **131047** — which is the window working, not a broken setup.

### C5. Talk to it

Message your test number from your phone. Expect, in order: blue ticks within
about a second, then an answer three to five seconds later. The worker log
prints one line per turn with the intents, the cards retrieved, the top score,
the token counts and the total time.

---

## Part D — Production

### D1. Deployment shape

Two processes against one database:

| Process | Command | Public? |
|---|---|---|
| API | `npm start -w @rag/api` | yes — Meta must reach `/webhooks/whatsapp` |
| Worker | `npm start -w @rag/worker` | no |

The worker has no HTTP port and needs no ingress. Both read the same `.env`.

**HTTPS is not optional.** Meta refuses a plain-HTTP callback URL and will not
follow a redirect to reach one.

### D2. Set the callback URL to the real host

Same form as C3, with the production URL. A saved webhook stays pointed at
whatever URL was last verified, so this is a step people forget after the first
successful ngrok test.

### D3. Before real customers

- The temporary Meta number is limited to five verified recipients. Register the
  client's real number under **WhatsApp → API Setup → Add phone number**, then
  update `WHATSAPP_PHONE_NUMBER_ID`.
- The app must be in **Live** mode, not Development.
- Business verification is required before the number can message anyone who has
  not been added to the test list. It takes days, not minutes — start it early.
- Check `WHATSAPP_RATE_LIMIT_PER_MINUTE` against expected traffic. Every turn is
  one Gemini embedding call plus one Claude call, both billed.

### D4. What to watch

The worker logs one JSON object per turn. Worth an alert:

| Line | Means |
|---|---|
| `whatsapp reply failed` | a turn threw; pg-boss will retry twice |
| `reply could not be delivered — not retrying` | a 400/401 from Meta. Check `authFailure` — an expired token stops **all** sending |
| `dropping reply — 24-hour window closed` | jobs are running late; check queue depth |
| `rate limit hit` | one number is flooding, or the ceiling is too low |
| `whatsapp message failed` with code 131026 | recipient cannot receive messages — usually not on WhatsApp |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Meta says "callback URL couldn't be validated" | verify token mismatch, or the API is not reachable | Compare `WHATSAPP_VERIFY_TOKEN` to the form; `curl` the ngrok URL |
| Every delivery rejected 403, secret is definitely right | the raw body was consumed before the signature check | `whatsappRouter` must be mounted **above** `express.json` in `server.ts` |
| Webhook returns 200, nothing ever replies | the worker is not running, or is on the wrong connection string | Worker log should say `worker started`; pg-boss needs `DIRECT_URL`, never the pooled one |
| Worked yesterday, all sends now 401 | the 24-hour test token expired | Issue a permanent System User token — Part A3 |
| Replies stop for one person only | that conversation is handed off, or their window closed | Check `Conversation.handoffState` and `lastInboundAt` |
| Same question answered twice | duplicate guard bypassed | `Message.waMessageId` must be written **inside** `persistTurn`'s transaction |
| Error 131047 on every send | outside the 24-hour window | The recipient must write first, or use an approved template |
| Error 131030 | recipient not in the test list | Add them under **API Setup → To → Manage phone number list** |
| `enqueued: 0` in the webhook log | the payload had no messages — a receipt, not a question | Normal. Receipts outnumber messages roughly three to one |
| Long answers arrive in two bubbles | over 4096 characters | Expected; `splitForSend` splits on a paragraph boundary |

---

## What this does not do yet

- **Photos, voice notes and PDFs** are acknowledged with a sentence, not read.
  `MessageAttachment` and `InboundMessage.mediaId` are ready; the missing piece
  is `apps/worker/src/jobs/mediaDownload.ts` and a storage provider.
- **Templates** for re-engaging someone outside the window: `sendTemplate` works
  and is tested, but nothing calls it. Deciding when to re-engage is a product
  decision.
- **Menus.** `sendButtons`, `sendList` and the row-budget helpers are here and
  tested, and a tap already arrives as `InboundMessage.replyId`. Nothing builds
  a menu tree — this agent answers questions rather than offering options.
