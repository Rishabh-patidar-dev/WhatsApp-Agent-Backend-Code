import { answer, type QueueJob } from '@rag/core';
import type { CardSearchPort, EmbeddingProvider, LlmProvider, VerificationPort } from '@rag/core';
import {
  WhatsAppApiError,
  WhatsAppWindowClosedError,
  assertWindowOpen,
  type WhatsAppClient,
  type WhatsAppReplyJob,
} from '@rag/channels';
import type { DbClient } from '@rag/db';
import { describeError } from '@rag/shared';
import type { Logger } from '@rag/shared';
import { loadTurnContext, persistTurn, resolveContact } from '@rag/providers';

/**
 * One WhatsApp turn, end to end.
 *
 * This is `apps/api/src/routes/chat.ts` without the stream, and deliberately
 * so: both channels call the same `answer()` with the same context built by the
 * same `loadTurnContext`, and persist through the same `persistTurn`. If the
 * same question gets two different answers depending on where it was asked,
 * that is a bug in one of those three, not something either channel gets to
 * fix locally.
 *
 * What is genuinely different here is everything around the answer:
 *
 *   · the reply is *sent*, not returned, so a delivery failure is a real
 *     outcome that has to be recorded
 *   · the job can run long after the message arrived, so the 24-hour window
 *     is checked rather than assumed
 *   · pg-boss retries, so every step has to be safe to run twice
 *
 * ── Ordering, and why persist comes before send ──────────────────────────
 *
 *   read receipt → context → answer → persist → send → record the send
 *
 * Persisting before sending means a database failure costs a retry rather than
 * producing a reply the dashboard has no record of. An admin watching a live
 * conversation, and the compliance trail for a quoted price, both depend on the
 * transcript being complete — an unauditable answer is worse than a late one.
 * The reverse order would also break the retry story below.
 */

export interface WhatsAppReplyDeps {
  db: DbClient;
  client: WhatsAppClient;
  search: CardSearchPort;
  verification: VerificationPort;
  embeddings: EmbeddingProvider;
  llm: LlmProvider;
  logger: Logger;
  /** Prior messages replayed into the prompt. */
  historyTurns: number;
  /** Longest question accepted. Longer is truncated, never rejected. */
  maxQueryChars: number;
  /** Turns per minute per phone number. Each one is two billed API calls. */
  rateLimitPerMinute: number;
}

/** Spanish, because the agent is. Kept here rather than in a prompt: these are
 *  said *instead of* asking the model, so they must not depend on it. */
const COPY = {
  unsupported:
    'Por ahora solo puedo leer mensajes de texto. Si me escribes tu pregunta sobre los ' +
    'cursos de Cruz Roja, con gusto te ayudo.',
  rateLimited:
    'Estoy respondiendo tus mensajes anteriores. Dame unos segundos y continuamos.',
} as const;

export async function whatsappReply(
  job: QueueJob<WhatsAppReplyJob>,
  deps: WhatsAppReplyDeps,
  guard: RateGuard,
): Promise<void> {
  const { db, client } = deps;
  const data = job.data;
  const log = deps.logger.child({
    job: job.name,
    jobId: job.id,
    waMessageId: data.waMessageId,
    contact: maskPhone(data.from),
  });
  const startedAt = Date.now();

  // ── 0. has this message already been answered? ───────────────────────
  //
  // `sendUnique` in the webhook deduplicates a redelivery that arrives while
  // the first job is still queued. It cannot help with one that arrives after
  // that job finished — Meta will redeliver a message it considers unanswered
  // for hours. `Message.waMessageId` is unique for exactly this, and checking
  // it here means the duplicate costs one indexed lookup instead of an
  // embedding call, a model call, and a second identical reply.
  const prior = await priorTurn(db, data.waMessageId);

  if (prior.kind === 'answered') {
    log.info('duplicate delivery ignored — already answered');
    return;
  }

  if (prior.kind === 'unsent') {
    // The previous attempt generated and stored an answer, then failed to send
    // it. Re-sending the stored text is both cheaper and more correct than
    // regenerating: the customer gets the answer the transcript says they got.
    log.warn('resending a stored reply that was never delivered', { messageId: prior.messageId });
    await sendAndRecord(deps, prior.messageId, data.from, prior.content, log);
    return;
  }

  // ── 1. blue ticks ────────────────────────────────────────────────────
  //
  // Sent before the work, not after: the point is to fill the three to five
  // seconds the answer takes with some sign of life. Failure is swallowed —
  // a missing tick is cosmetic and must never cost the reply.
  void client.markRead(data.waMessageId).catch((error: unknown) => {
    log.debug('mark-read failed', { reason: describeError(error) });
  });

  // ── 2. who is this ───────────────────────────────────────────────────
  const { contactId, conversationId } = await resolveContact(db, {
    channel: 'WHATSAPP',
    externalId: data.from,
  });

  // The WhatsApp profile name is the only name we get for free. Written once
  // and never overwritten: a name captured in conversation, or corrected by an
  // admin, is better evidence than a display name the person may have set to
  // an emoji.
  if (data.profileName) {
    await db.contact.updateMany({
      where: { id: contactId, fullName: null },
      data: { fullName: data.profileName },
    });
  }

  const context = await loadTurnContext({
    db,
    conversationId,
    contactId,
    historyTurns: deps.historyTurns,
  });

  // ── 3. a human has this conversation ─────────────────────────────────
  //
  // Silence, not an apology. The person is mid-conversation with a member of
  // the Cruz Roja team; a bot interjecting to say it is staying quiet is
  // exactly the interruption the handoff exists to prevent.
  if (context.handedOff) {
    log.info('skipped — conversation is handed off to a human', { conversationId });
    return;
  }

  // ── 4. anything that is not text ─────────────────────────────────────
  const question = data.text.trim().slice(0, deps.maxQueryChars);
  if (data.kind === 'unsupported' || question === '') {
    log.info('acknowledging unsupported message', { mediaType: data.mediaType });
    await acknowledge(deps, {
      conversationId,
      contactId,
      waMessageId: data.waMessageId,
      from: data.from,
      inbound: data.mediaType ? `[${data.mediaType}]` : '[mensaje sin texto]',
      reply: COPY.unsupported,
    });
    return;
  }

  // ── 5. cost ceiling ──────────────────────────────────────────────────
  if (!guard.allow(data.from)) {
    log.warn('rate limit hit', { perMinute: deps.rateLimitPerMinute });
    // Once per window, not per message: the reason for the limit is a flood,
    // and answering a flood with a flood of apologies helps nobody.
    if (guard.shouldWarn(data.from)) {
      await client.sendText(data.from, COPY.rateLimited).catch((error: unknown) => {
        log.debug('rate-limit notice failed to send', { reason: describeError(error) });
      });
    }
    return;
  }

  // ── 6. is the window still open ──────────────────────────────────────
  //
  // Measured from when the webhook accepted this message, not from
  // `Conversation.lastInboundAt`: the row may have moved on, and what matters
  // is whether *this* message still entitles us to a free-form reply. Normally
  // seconds ago. Not normally, this is the third retry of a job that has been
  // failing since yesterday, and sending would earn a 131047 that reads like
  // an authentication failure.
  try {
    assertWindowOpen(data.from, data.receivedAt);
  } catch (error) {
    if (error instanceof WhatsAppWindowClosedError) {
      log.error('dropping reply — 24-hour window closed before the job ran', {
        receivedAt: data.receivedAt,
        expiredAt: error.state.expiresAt?.toISOString() ?? null,
      });
      // Not rethrown. A retry cannot make the window reopen, and pg-boss would
      // spend three attempts learning that. Reaching this person again needs an
      // approved template, which is a decision for a human.
      return;
    }
    throw error;
  }

  // ── 7. the answer ────────────────────────────────────────────────────
  const result = await answer(
    {
      query: question,
      channel: 'WHATSAPP',
      audienceType: context.audienceType,
      audienceSubtype: context.audienceSubtype,
      history: context.history,
      contactSummary: context.contactSummary,
    },
    {
      search: deps.search,
      verification: deps.verification,
      embeddings: deps.embeddings,
      llm: deps.llm,
    },
  );

  // ── 8. the record ────────────────────────────────────────────────────
  //
  // `waMessageId` on the user message is what makes step 0 work on the next
  // delivery of this same message. It is written inside `persistTurn`'s
  // transaction so the unique constraint is the real guard — two workers
  // racing on the same message end with one transaction rolled back rather
  // than two replies sent.
  const persisted = await persistTurn({
    db,
    conversationId,
    contactId,
    channel: 'WHATSAPP',
    userQuery: question,
    result,
    waMessageId: data.waMessageId,
  });

  // ── 9. send ──────────────────────────────────────────────────────────
  await sendAndRecord(deps, persisted.assistantMessageId, data.from, result.text, log);

  log.info('whatsapp turn answered', {
    conversationId,
    messageId: persisted.assistantMessageId,
    intents: result.intents,
    resolution: result.resolution.status,
    cards: result.cards.length,
    topScore: result.topScore,
    knowledgeGap: result.knowledgeGap,
    routed: result.routing.length,
    promptTokens: result.promptTokens,
    outputTokens: result.outputTokens,
    cachedTokens: result.cachedTokens,
    modelLatencyMs: result.latencyMs,
    totalMs: Date.now() - startedAt,
  });
}

// ── sending ────────────────────────────────────────────────────────────

/**
 * Send one stored assistant message and write down what happened.
 *
 * The `waMessageId` is written only after Meta has accepted the message, which
 * is what makes it a reliable "was this delivered" test on a later retry —
 * `status` alone would not be, since it defaults to SENT in the schema.
 */
async function sendAndRecord(
  deps: WhatsAppReplyDeps,
  assistantMessageId: string,
  to: string,
  text: string,
  log: Logger,
): Promise<void> {
  try {
    const sent = await deps.client.sendText(to, text);

    await deps.db.message.update({
      where: { id: assistantMessageId },
      data: {
        // The first part's id. A split answer produces several, and this is the
        // one Meta's delivery receipts refer to.
        waMessageId: sent.messageIds[0] ?? null,
        status: 'SENT',
      },
    });

    if (sent.messageIds.length > 1) {
      log.info('reply was split across several messages', { parts: sent.messageIds.length });
    }
  } catch (error) {
    const reason = describeError(error);

    await deps.db.message.update({
      where: { id: assistantMessageId },
      data: { status: 'FAILED', failureReason: reason.slice(0, 500) },
    });

    // An expired token or a blocked recipient will fail identically on every
    // retry. Recorded and swallowed: the transcript now shows an undelivered
    // answer, which is what the dashboard needs, and three more attempts would
    // add nothing but log noise.
    if (error instanceof WhatsAppApiError && !error.retryable) {
      log.error('reply could not be delivered — not retrying', {
        status: error.status,
        code: error.code,
        windowExpired: error.isWindowExpired,
        authFailure: error.isAuthFailure,
        reason,
      });
      return;
    }

    // Anything else may be transient. Rethrown so pg-boss retries — step 0
    // will find the stored answer and re-send it rather than regenerate.
    throw error;
  }
}

/**
 * Store and send a canned line, without involving the model.
 *
 * A photo, a voice note or a location is not a question `answer()` can take,
 * but the person is still waiting. This keeps the transcript complete — the
 * inbound message is recorded with its `waMessageId`, so the duplicate guard
 * covers it exactly as it covers a real turn.
 */
async function acknowledge(
  deps: WhatsAppReplyDeps,
  input: {
    conversationId: string;
    contactId: string;
    waMessageId: string;
    from: string;
    inbound: string;
    reply: string;
  },
): Promise<void> {
  const now = new Date();

  const assistantMessageId = await deps.db.$transaction(async (tx) => {
    await tx.message.create({
      data: {
        conversationId: input.conversationId,
        role: 'USER',
        content: input.inbound,
        language: 'es',
        waMessageId: input.waMessageId,
      },
      select: { id: true },
    });

    const assistant = await tx.message.create({
      data: {
        conversationId: input.conversationId,
        role: 'ASSISTANT',
        content: input.reply,
        language: 'es',
        usedFallback: true,
        status: 'PENDING',
      },
      select: { id: true },
    });

    await tx.conversation.update({
      where: { id: input.conversationId },
      data: { lastInboundAt: now, lastOutboundAt: now },
    });

    await tx.contact.update({
      where: { id: input.contactId },
      data: { lastSeenAt: now, messageCount: { increment: 2 } },
    });

    return assistant.id;
  });

  await sendAndRecord(deps, assistantMessageId, input.from, input.reply, deps.logger);
}

// ── idempotency ────────────────────────────────────────────────────────

type PriorTurn =
  | { kind: 'none' }
  | { kind: 'answered' }
  | { kind: 'unsent'; messageId: string; content: string };

async function priorTurn(db: DbClient, waMessageId: string): Promise<PriorTurn> {
  const userMessage = await db.message.findUnique({
    where: { waMessageId },
    select: { id: true, conversationId: true, createdAt: true },
  });
  if (!userMessage) return { kind: 'none' };

  // The assistant message written in the same transaction as this one. `gte`
  // rather than `gt` because both rows can land on the same millisecond.
  const reply = await db.message.findFirst({
    where: {
      conversationId: userMessage.conversationId,
      role: 'ASSISTANT',
      createdAt: { gte: userMessage.createdAt },
    },
    orderBy: { createdAt: 'asc' },
    select: { id: true, content: true, waMessageId: true },
  });

  // No reply at all should be impossible — they are written together. If it
  // happens, regenerating would violate the unique constraint on the user
  // message and fail the job for good, so treat it as answered and move on.
  if (!reply) return { kind: 'answered' };
  if (reply.waMessageId) return { kind: 'answered' };

  return { kind: 'unsent', messageId: reply.id, content: reply.content };
}

// ── cost ceiling ───────────────────────────────────────────────────────

/**
 * A ceiling on turns per phone number, per minute.
 *
 * Every turn is one Gemini embedding call plus one Claude call, both billed,
 * and WhatsApp makes it trivial to hold the send key down. The web widget's
 * equivalent lives in `apps/api/src/middleware/rateLimit.ts`; it cannot help
 * here, because from the API's point of view every webhook arrives from Meta's
 * own address range.
 *
 * In-process, like that one, and honest about it: the worker is the process
 * that spends the money, and there is one of it. A second worker instance
 * doubles the effective limit, which is the day this moves into Postgres.
 */
export class RateGuard {
  private readonly hits = new Map<string, number[]>();
  private readonly warned = new Map<string, number>();

  constructor(
    private readonly perMinute: number,
    private readonly windowMs = 60_000,
  ) {}

  allow(key: string, now = Date.now()): boolean {
    const recent = (this.hits.get(key) ?? []).filter((at) => now - at < this.windowMs);

    if (recent.length >= this.perMinute) {
      this.hits.set(key, recent);
      return false;
    }

    recent.push(now);
    this.hits.set(key, recent);

    // Cheap eviction: without it the map grows by one entry per number that
    // ever writes in, for the life of the process.
    if (this.hits.size > 5_000) this.sweep(now);
    return true;
  }

  /** True once per window, so a flood earns one notice rather than fifty. */
  shouldWarn(key: string, now = Date.now()): boolean {
    const last = this.warned.get(key) ?? 0;
    if (now - last < this.windowMs) return false;
    this.warned.set(key, now);
    return true;
  }

  private sweep(now: number): void {
    for (const [key, stamps] of this.hits) {
      if (stamps.every((at) => now - at >= this.windowMs)) this.hits.delete(key);
    }
    for (const [key, at] of this.warned) {
      if (now - at >= this.windowMs) this.warned.delete(key);
    }
  }
}

/** `…4821`. Enough to correlate a conversation, not enough to identify a person. */
function maskPhone(phone: string): string {
  return phone.length > 4 ? `…${phone.slice(-4)}` : '…';
}

/** Bind the dependencies once; the queue calls what comes back. */
export function makeWhatsAppReplyHandler(deps: WhatsAppReplyDeps) {
  const guard = new RateGuard(deps.rateLimitPerMinute);

  return async (job: QueueJob<WhatsAppReplyJob>): Promise<void> => {
    try {
      await whatsappReply(job, deps, guard);
    } catch (error) {
      // Rethrown so pg-boss records the failure and retries; logged here
      // because the job id is only in scope on this side of the boundary.
      deps.logger.error('whatsapp reply failed', {
        jobId: job.id,
        waMessageId: job.data?.waMessageId,
        reason: describeError(error),
        error,
      });
      throw error;
    }
  };
}
