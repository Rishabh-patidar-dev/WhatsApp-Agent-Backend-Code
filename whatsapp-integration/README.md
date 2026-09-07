# WhatsApp integration for `cruzroja-agent`

Drop-in code that connects the existing RAG agent to WhatsApp, plus the steps to
wire it to Meta.

The monorepo was built with this channel's shape already carved out: four empty
files under `packages/channels/src/whatsapp/`, an empty webhook route, an empty
worker job, a `TODO (WhatsApp channel)` marking the exact line in `server.ts`
where the router must mount, and a `JOB_WHATSAPP_REPLY` constant already shared
between the API and the worker. This bundle fills those in and changes nothing
else.

## What is here

| | |
|---|---|
| **`INTEGRATION_STEPS.md`** | Meta setup, `.env`, running it locally, going to production, troubleshooting. **Start here.** |
| **`WHATSAPP_FILE_MAP.md`** | Every file, where it goes, and why — new versus edited. |
| **`WHATSAPP_PROGRAM_FLOW.md`** | How it works: sequence diagram, function call graph, data flow, the decision tree, failure paths. |
| **`files/`** | The code, mirroring the repo tree. `cp -r files/. /path/to/cruzroja-agent/` |
| **`patches/whatsapp-integration.diff`** | The thirteen edits as a 605-line unified diff, for review before applying. |

## The short version

```bash
cp -r files/. /path/to/cruzroja-agent/
cd /path/to/cruzroja-agent

# four values from the Meta App dashboard — INTEGRATION_STEPS.md Part A
$EDITOR .env

npm install
npm run typecheck
npm test -w @rag/channels     # 48 cases

npm run dev:api               # terminal 1
npm run dev:worker            # terminal 2
ngrok http 3001               # terminal 3 — the callback URL Meta needs

npm run checkpoint:10         # proves the whole path without touching Meta
```

## Design in one paragraph

The webhook does no thinking. Meta allows about two seconds before it decides
an endpoint is broken and redelivers; a real answer takes three to five. So
`apps/api` verifies the signature over the raw request bytes, writes one job per
message keyed on the WhatsApp message id, and answers 200 in about twenty
milliseconds. `apps/worker` picks the job up and runs the same `answer()` the
web widget uses — retrieve, resolve the course, verify the price against the
database, check the compliance flags, generate, format for WhatsApp — persists
the whole turn in one transaction, then sends. Three separate guards make a
redelivery cheap rather than duplicated, and the 24-hour window is checked
before the send rather than discovered through a Graph API error.

## State

- All six hand-written source files typecheck under the repo's own
  `tsconfig.base.json` — `strict`, `noUncheckedIndexedAccess`,
  `verbatimModuleSyntax`, `isolatedModules`, NodeNext — with the two middleware
  files checked against real express 5 types and the route and worker job
  checked against faithful stubs of `@rag/core`, `@rag/db`, `@rag/providers`
  and `@rag/shared`.
- The 48 channel tests pass under vitest.
- What that does *not* cover: Prisma field names. The stub types database rows
  loosely, so a wrong column would slip through. Run `npm run db:generate &&
  npm run typecheck` after copying the bundle in — that is the check the stub
  cannot be.
- No new dependencies anywhere. The Graph API client uses global `fetch`.
