# How the WhatsApp agent actually works

One message, from the moment someone taps send to the moment the answer appears
on their phone — every function it passes through, in order, with what each one
reads and writes.

The diagrams are Mermaid; GitHub renders them inline.

---

## 1. The shape of it

Two processes, one queue between them. That split is the single most important
fact about this design.

```mermaid
flowchart LR
    P["Person on WhatsApp"] -->|"1 - message"| M["Meta Cloud API"]
    M -->|"2 - POST webhook"| API["apps/api<br/>routes/whatsapp.ts"]
    API -->|"3 - 200 OK in ~20ms"| M
    API -->|"4 - enqueue job"| Q[("pg-boss<br/>whatsapp.reply")]
    Q -->|"5 - claim job"| W["apps/worker<br/>jobs/whatsappReply.ts"]
    W <-->|"6 - read + write"| DB[("Postgres<br/>32 tables")]
    W -->|"7 - retrieve, verify, generate"| CORE["packages/core<br/>answer()"]
    W -->|"8 - send reply"| M
    M -->|"9 - deliver"| P

    style API fill:#e8f0fe,stroke:#4285f4
    style W fill:#e6f4ea,stroke:#34a853
    style Q fill:#fef7e0,stroke:#f9ab00
```

**Why not answer inside the webhook.** Meta expects a 2xx within a couple of
seconds; miss it and the delivery is marked failed and redelivered, repeatedly,
and a webhook that stays slow is eventually unsubscribed. A real answer takes
three to five seconds — embed the question, search 104 cards, verify the price
against the database, call Claude. Those two facts cannot both be satisfied on
one thread, so the webhook does nothing but check the signature, write a row,
and answer 200.

---

## 2. One message, end to end

```mermaid
sequenceDiagram
    autonumber
    participant U as Person
    participant Meta as Meta Cloud API
    participant R as routes/whatsapp.ts
    participant MW as rawBody + verifyMetaSignature
    participant PW as parseWebhook.ts
    participant Q as PgBossQueue
    participant J as jobs/whatsappReply.ts
    participant DB as Postgres
    participant A as core/answer()
    participant C as whatsapp/client.ts

    U->>Meta: "¿Cuánto cuesta el curso de RCP?"
    Meta->>R: POST /webhooks/whatsapp
    R->>MW: raw bytes kept, HMAC-SHA256 checked
    MW-->>R: signature valid
    R->>PW: parseWebhook(body)
    PW-->>R: 1 message, 0 statuses
    R->>Q: sendUnique("whatsapp.reply", job, key = waMessageId)
    R-->>Meta: 200 OK

    Note over R,Meta: everything above finishes in ~20ms

    Q->>J: job claimed
    J->>DB: priorTurn(waMessageId) - already answered?
    DB-->>J: no
    J-->>Meta: markRead - blue ticks
    J->>DB: resolveContact - Contact + Conversation
    J->>DB: loadTurnContext - history, summary, audience, handoff
    DB-->>J: 6 turns, audience = INDIVIDUAL, handoff = BOT
    J->>J: assertWindowOpen - is the 24h window still open?

    J->>A: answer({ query, channel: "WHATSAPP", ... })
    A->>DB: hybridSearch - vector + keyword over CourseCard
    A->>DB: resolveCourse - which course is meant
    A->>DB: verifyPrice - the real number, from CoursePricing
    A->>DB: checkFlags - is any field blocked
    A->>A: build prompt: system + cards + DATOS VERIFICADOS + history
    A-->>J: AnswerResult - formatted for WhatsApp

    J->>DB: persistTurn - both messages, retrievals, tool calls, routing
    DB-->>J: assistantMessageId
    J->>C: sendText(to, result.text)
    C->>Meta: POST /v21.0/{phoneNumberId}/messages
    Meta-->>C: { messages: [{ id: "wamid.OUT" }] }
    J->>DB: Message.waMessageId = wamid.OUT, status = SENT
    Meta->>U: the answer

    Meta->>R: POST - status: delivered
    R->>DB: Message.status = DELIVERED
```

---

## 3. Function by function

Each arrow is a real call. File paths are relative to the repo root.

```mermaid
flowchart TD
    subgraph API["apps/api — public, fast, does no thinking"]
        S["server.ts<br/>createApp"] --> WR["routes/whatsapp.ts<br/>whatsappRouter"]
        WR --> GET["GET handler<br/>safeEqual(verify token)"]
        WR --> RB["middleware/rawBody.ts<br/>rawJsonBody"]
        RB --> VS["middleware/verifyMetaSignature.ts<br/>verifyMetaSignature"]
        VS --> HW["handleWebhook"]
        HW --> IW["parseWebhook.ts<br/>isWhatsAppWebhook"]
        HW --> PWF["parseWebhook.ts<br/>parseWebhook"]
        PWF --> PM["parseMessage"]
        PWF --> PS["parseStatus"]
        HW --> TRJ["parseWebhook.ts<br/>toReplyJob"]
        TRJ --> SU["PgBossQueue.sendUnique"]
        HW --> AS["applyStatuses<br/>Message.status, rank-guarded"]
    end

    SU -.->|"whatsapp.reply"| WK

    subgraph WORKER["apps/worker — private, slow, does the thinking"]
        WK["index.ts<br/>queue.work"] --> MH["jobs/whatsappReply.ts<br/>makeWhatsAppReplyHandler"]
        MH --> WRP["whatsappReply"]
        WRP --> PT["priorTurn<br/>duplicate guard"]
        WRP --> MR["client.markRead"]
        WRP --> RC["providers/persistTurn.ts<br/>resolveContact"]
        WRP --> LTC["providers/loadTurnContext.ts<br/>loadTurnContext"]
        WRP --> RG["RateGuard.allow"]
        WRP --> AWO["window.ts<br/>assertWindowOpen"]
        WRP --> ANS["core/answer()"]
        WRP --> PST["providers/persistTurn.ts<br/>persistTurn"]
        WRP --> SAR["sendAndRecord"]
        WRP --> ACK["acknowledge<br/>non-text messages"]
        SAR --> ST["client.ts<br/>WhatsAppClient.sendText"]
        ST --> SFS["format.ts<br/>splitForSend"]
        ST --> POST["client.post<br/>retry on 429/5xx only"]
    end

    subgraph CORE["packages/core — no vendor SDKs, no database"]
        ANS --> HS["retrieval/hybrid.ts<br/>hybridSearch"]
        ANS --> RCO["retrieval/resolveCourse.ts<br/>resolveCourse"]
        ANS --> VP["verification/verifyPrice.ts<br/>verifyPrice"]
        ANS --> CF["verification/checkFlags.ts<br/>checkFlags"]
        ANS --> SP["generation/prompts/system.ts<br/>SYSTEM_PROMPT_ES"]
        ANS --> LLM["LlmProvider.complete"]
        ANS --> FFW["prompts/whatsapp.ts<br/>formatForWhatsApp"]
    end

    style API fill:#e8f0fe
    style WORKER fill:#e6f4ea
    style CORE fill:#fce8e6
```

Note where the arrows stop. `packages/core` never calls the database and never
calls Meta — it is handed a `CardSearchPort` and a `LlmProvider` and asks those.
That is what lets the same `answer()` serve the web widget unchanged, and what
makes the eventual move to Bedrock a config change.

---

## 4. What the data looks like at each hop

```mermaid
flowchart LR
    A["Meta webhook JSON<br/>entry[].changes[].value.messages[]"]
      -->|"parseWebhook"| B["InboundMessage<br/>waMessageId, from, kind,<br/>text, profileName, replyId"]
    B -->|"toReplyJob"| C["WhatsAppReplyJob<br/>flat JSON in pgboss.job.data<br/>+ receivedAt"]
    C -->|"loadTurnContext"| D["TurnContext<br/>history[6], contactSummary,<br/>audienceType, handedOff"]
    D -->|"answer"| E["AnswerResult<br/>text, cards[], price,<br/>flags, routing[], tokens"]
    E -->|"persistTurn"| F["Message x2<br/>MessageRetrieval x3<br/>MessageToolCall x2<br/>RoutingEvent, KnowledgeGap"]
    E -->|"sendText"| G["Graph API payload<br/>messaging_product, to,<br/>type: text, text.body"]
    G -->|"Meta responds"| H["wamid.OUT<br/>→ Message.waMessageId"]
```

### Rows touched, in order

| Step | Table | Operation |
|---|---|---|
| `priorTurn` | `Message` | read by `waMessageId` — the unique column |
| `resolveContact` | `ContactIdentity` | read by `(kind, value)` |
| | `Contact` | create on first contact, with `refCode`, `phoneE164`, `phoneHash` |
| | `Conversation` | reuse the open one, or create |
| profile name | `Contact` | `updateMany` where `fullName IS NULL` — written once, never overwritten |
| `loadTurnContext` | `Conversation` | read `handoffState` |
| | `Message` | read last 6, newest first, reversed |
| | `Contact` | read `summary` |
| | `ContactFact` | read `AUDIENCE_TYPE`, `AUDIENCE_SUBTYPE` where `supersededAt IS NULL` |
| `answer` | `CourseCard` | vector + trigram search |
| | `Course`, `CoursePricing`, `CoursePricePackage` | price verification |
| | `ContentFlag` | compliance flags |
| `persistTurn` | `Message` | create user turn **with `waMessageId`**, and assistant turn |
| | `MessageRetrieval` | one row per card used |
| | `MessageToolCall` | the price and flag lookups — the audit trail |
| | `RoutingEvent` | when the answer hands off to Cruz Roja |
| | `KnowledgeGap` | when the top score was too weak; clustered on the normalised question |
| | `Conversation` | `lastInboundAt`, `lastOutboundAt` — these drive the 24-hour window |
| | `Contact` | `lastSeenAt`, `messageCount += 2`, `lastIntent` |
| `sendAndRecord` | `Message` | assistant row gets `waMessageId` and `status` |
| receipts | `Message` | `status` advanced to `DELIVERED` / `READ` |

Everything from `persistTurn` down happens in **one transaction**. An answer
that was sent but whose routing event was lost is worse than no record at all —
the dashboard would show a handled question that nobody is handling.

---

## 5. The decisions inside the worker

Seven branches before the model is ever called. Six of them end the turn.

```mermaid
flowchart TD
    START(["job claimed"]) --> DUP{"already answered?<br/>priorTurn"}
    DUP -->|"answered"| E1(["skip — Meta redelivered"])
    DUP -->|"generated but never sent"| RESEND["re-send the stored text"]
    RESEND --> E2(["done — no second model call"])
    DUP -->|"new"| READ["markRead — blue ticks"]

    READ --> RESOLVE["resolveContact<br/>loadTurnContext"]
    RESOLVE --> HAND{"handoffState?"}
    HAND -->|"HUMAN_REQUESTED<br/>HUMAN_ACTIVE"| E3(["silence — a person has this"])
    HAND -->|"BOT / RETURNED_TO_BOT"| KIND

    KIND{"message kind?"} -->|"image, audio,<br/>document, location"| ACKN["acknowledge<br/>canned line, still recorded"]
    ACKN --> E4(["done — no model call"])
    KIND -->|"text or tap"| RATE

    RATE{"under the<br/>per-number limit?"} -->|"no"| WARN["one notice per window"]
    WARN --> E5(["done"])
    RATE -->|"yes"| WIN

    WIN{"24h window<br/>still open?"} -->|"closed"| E6(["log and drop —<br/>needs an approved template"])
    WIN -->|"open"| GEN["answer() — retrieve, verify, generate"]

    GEN --> PERSIST["persistTurn — one transaction"]
    PERSIST --> SEND{"Meta accepted?"}
    SEND -->|"yes"| OK(["waMessageId + SENT"])
    SEND -->|"400 / 401 / 131047"| FAIL(["FAILED + reason,<br/>not retried"])
    SEND -->|"429 / 5xx / network"| RETRY(["FAILED + reason,<br/>rethrown → pg-boss retries"])

    style E1 fill:#f1f3f4
    style E3 fill:#f1f3f4
    style E6 fill:#fce8e6
    style OK fill:#e6f4ea
```

---

## 6. Why a redelivery cannot answer twice

Meta redelivers anything it believes failed — including deliveries that
succeeded but answered slowly. Three guards, in order of cost:

```mermaid
flowchart LR
    R1["Redelivery arrives"] --> G1{"sendUnique<br/>singletonKey = waMessageId"}
    G1 -->|"first job still queued"| D1(["enqueues nothing<br/>cost: one INSERT attempt"])
    G1 -->|"first job finished"| G2{"priorTurn<br/>Message.waMessageId"}
    G2 -->|"found + sent"| D2(["skip<br/>cost: one indexed read"])
    G2 -->|"found, never sent"| D3(["re-send stored text<br/>cost: one Graph API call"])
    G2 -->|"not found"| G3{"unique constraint<br/>inside persistTurn"}
    G3 -->|"two workers raced"| D4(["transaction rolls back<br/>cost: one wasted turn"])
    G3 -->|"clear"| D5(["answer normally"])
```

The third guard is why `persistTurn` writes `waMessageId` inside its
transaction rather than in an update afterwards. Written after, two workers
racing on the same message both pass the check and both reply.

---

## 7. The 24-hour window

WhatsApp is not email. Once a person messages the business, Meta opens a
24-hour window in which any free-form reply is allowed. After it closes,
free-form sends are refused with error **131047**, and the only way to reach
that person is an approved template.

```mermaid
flowchart LR
    T0["T+0<br/>person writes"] -->|"window opens<br/>Conversation.lastInboundAt"| OPEN
    OPEN["T+0 → T+24h<br/>free-form replies allowed<br/>the agent answers normally"] -->|"24h elapse"| SHUT
    SHUT["T+24h<br/>window closed<br/>Graph API refuses free-form — 131047"] --> TPL["approved template only<br/>client.sendTemplate"]
    NEW["any new inbound message"] -.->|"restarts the clock"| T0

    style OPEN fill:#e6f4ea,stroke:#34a853
    style SHUT fill:#fce8e6,stroke:#d93025
```

Checking it before an inbound reply looks redundant — the window opened on the
very message being answered. It is not: a job on its third pg-boss retry, or one
that queued behind an outage, can run well after the message arrived. Without
the check, that turn burns an embedding call and a Claude call and then fails at
the Graph API with an error that reads like an authentication problem.

The arithmetic lives in one file, `packages/channels/src/whatsapp/window.ts`,
because three places need it: the worker before sending, the dashboard's
countdown, and any future admin "send a manual reply" route.

---

## 8. Failure, and what happens to it

| What fails | What happens | Who finds out |
|---|---|---|
| Bad or missing signature | 403, nothing enqueued | `warn` line with the request id |
| Wrong verify token on the handshake | 403 plain text | `warn` line; Meta shows the URL as unverified |
| Queue insert fails | 500 → **Meta redelivers** | `error` line; the message is not lost |
| Payload is not from WhatsApp | 200 `ignored` | `warn` — someone subscribed another product |
| Delivery receipt write fails | swallowed, answer unaffected | `warn` |
| Embedding or Claude call fails | job throws → 2 retries with backoff | `error` with `jobId` and `waMessageId` |
| `persistTurn` fails | nothing sent, job retries | `error`; no unauditable reply exists |
| Graph API 429 / 5xx | 3 attempts inside the client, then the job retries | `warn` per attempt |
| Graph API 400 / 401 / 131047 | `Message.status = FAILED` with the reason, **no retry** | `error` naming the Meta code |
| Window closed before the job ran | dropped, not retried | `error` with when it expired |
| Conversation handed to a human | nothing sent, job succeeds | `info` |

Two log conventions worth knowing: phone numbers are always masked to their last
four digits, and the message body is never logged — it is the customer's own
words, and once the answer quotes a price it is the client's commercial terms.

---

## 9. Both channels, one brain

```mermaid
flowchart TD
    WA["WhatsApp<br/>routes/whatsapp.ts → queue → whatsappReply.ts"] --> LTC
    WEB["Web widget<br/>routes/chat.ts, streamed inline"] --> LTC
    LTC["loadTurnContext<br/>history, summary, audience, handoff"] --> ANS
    ANS["answer({ channel })<br/>retrieve → resolve → verify → generate"] --> FMT
    FMT{"channel?"}
    FMT -->|"WHATSAPP"| F1["formatForWhatsApp<br/>4 marks, short, plain"]
    FMT -->|"WEB"| F2["formatForWeb<br/>markdown, links"]
    F1 --> PST["persistTurn"]
    F2 --> PST
    PST --> DASH["apps/dashboard<br/>reads the same rows"]
```

The channels differ in three things and three things only: how the message
arrives, how the answer is formatted, and how it leaves. Retrieval, price
verification, compliance flags, the prompt and the audit trail are the same code
for both — which is the point. If the same question got two different answers
depending on where it was asked, that would be a bug in the middle, not
something either channel gets to fix locally.

---

## 10. Reading order, if you are new to this

1. `packages/channels/src/whatsapp/parseWebhook.ts` — what Meta actually sends
2. `apps/api/src/routes/whatsapp.ts` — the two-second rule in practice
3. `apps/worker/src/jobs/whatsappReply.ts` — the turn, top to bottom
4. `packages/core/src/generation/answer.ts` — the part that was already there
5. `scripts/checkpoint10-whatsapp.ts` — how to prove all of it works
