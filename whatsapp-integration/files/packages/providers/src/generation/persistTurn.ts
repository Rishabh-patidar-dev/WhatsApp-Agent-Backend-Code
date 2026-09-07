import { createHash } from 'node:crypto';
import type { DbClient } from '@rag/db';
import type { AnswerResult } from '@rag/core';
import { retrievalRows } from '@rag/core';

/**
 * Persist one turn: the user's message, the assistant's, and everything that
 * makes the answer auditable afterwards.
 *
 * RAG_PLAN §9.3 lists what has to survive a turn — `Message` with token counts
 * and `topScore`, `MessageRetrieval`, `MessageToolCall`, `RoutingEvent` when it
 * fired, `KnowledgeGap` when nothing scored. All of it goes in one transaction:
 * an answer that was sent but whose routing event was lost is worse than no
 * record, because the dashboard would show a handled question nobody handles.
 */

export interface ResolvedContact {
  contactId: string;
  conversationId: string;
}

export interface PersistTurnInput {
  db: DbClient;
  conversationId: string;
  contactId: string;
  channel: 'WHATSAPP' | 'WEB';
  userQuery: string;
  result: AnswerResult;
  /**
   * Meta's id for the inbound message, on the WhatsApp path.
   *
   * `Message.waMessageId` is unique, and writing it inside this transaction is
   * what makes that constraint a real idempotency guard: two workers handling
   * the same redelivered webhook end with one transaction rolled back rather
   * than two identical replies sent. Absent for web, which has no such id.
   */
  waMessageId?: string | null;
}

export interface PersistTurnResult {
  userMessageId: string;
  assistantMessageId: string;
  routingEventIds: string[];
  knowledgeGapId: string | null;
}

/** Lowercased and accent-stripped — the KnowledgeGap clustering key. */
export function normaliseForClustering(query: string): string {
  return query
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export async function persistTurn({
  db,
  conversationId,
  contactId,
  channel,
  userQuery,
  result,
  waMessageId,
}: PersistTurnInput): Promise<PersistTurnResult> {
  return db.$transaction(async (tx) => {
    const now = new Date();

    const userMessage = await tx.message.create({
      data: {
        conversationId,
        role: 'USER',
        content: userQuery,
        language: 'es',
        detectedIntent: result.intents[0] ?? null,
        resolvedCourseIds: result.resolution.candidates.map((c) => c.courseId),
        ...(waMessageId ? { waMessageId } : {}),
      },
      select: { id: true },
    });

    const assistantMessage = await tx.message.create({
      data: {
        conversationId,
        role: 'ASSISTANT',
        content: result.text,
        language: 'es',
        topScore: result.topScore,
        groundedInTools: result.groundedInTools,
        usedFallback: result.knowledgeGap,
        flagsTriggered: [...result.flagsTriggered],
        promptTokens: result.promptTokens,
        outputTokens: result.outputTokens,
        latencyMs: result.latencyMs,
        modelId: result.modelId,
      },
      select: { id: true },
    });

    const retrievals = retrievalRows(result);
    if (retrievals.length > 0) {
      await tx.messageRetrieval.createMany({
        data: retrievals.map((row) => ({ messageId: assistantMessage.id, ...row })),
        skipDuplicates: true,
      });
    }

    if (result.toolCalls.length > 0) {
      await tx.messageToolCall.createMany({
        data: result.toolCalls.map((call) => ({
          messageId: assistantMessage.id,
          toolName: call.toolName,
          arguments: call.arguments as object,
          result: call.result as object,
          success: call.success,
          errorText: call.errorText,
          latencyMs: call.latencyMs,
        })),
      });
    }

    const routingEventIds: string[] = [];
    for (const event of result.routing) {
      const row = await tx.routingEvent.create({
        data: {
          conversationId,
          messageId: assistantMessage.id,
          contactId,
          reason: event.reason,
          courseId: event.courseId,
          branchId: event.branchId,
          // Verbatim: this is both the compliance record and the lead.
          userQuery: event.userQuery,
        },
        select: { id: true },
      });
      routingEventIds.push(row.id);
    }

    // A weak top score means the corpus probably does not hold the answer.
    // Clustered on the normalised question so the fix-list groups rephrasings.
    let knowledgeGapId: string | null = null;
    if (result.knowledgeGap) {
      const normalised = normaliseForClustering(userQuery);
      const existing = await tx.knowledgeGap.findFirst({
        where: { normalised, resolved: false },
        select: { id: true },
      });

      const row = existing
        ? await tx.knowledgeGap.update({
            where: { id: existing.id },
            data: { occurrences: { increment: 1 }, lastSeenAt: now, topScore: result.topScore },
            select: { id: true },
          })
        : await tx.knowledgeGap.create({
            data: { query: userQuery, normalised, channel, topScore: result.topScore },
            select: { id: true },
          });
      knowledgeGapId = row.id;
    }

    await tx.conversation.update({
      where: { id: conversationId },
      data: { lastInboundAt: now, lastOutboundAt: now },
    });

    await tx.contact.update({
      where: { id: contactId },
      data: {
        lastSeenAt: now,
        messageCount: { increment: 2 },
        lastIntent: result.intents[0] ?? null,
        ...(result.routing.length > 0
          ? { routingCount: { increment: result.routing.length } }
          : {}),
      },
    });

    return {
      userMessageId: userMessage.id,
      assistantMessageId: assistantMessage.id,
      routingEventIds,
      knowledgeGapId,
    };
  });
}

/**
 * Find or create the contact and conversation for an incoming turn.
 *
 * `ContactIdentity` is the lookup table, not `Contact` itself: a person may
 * arrive by phone today and by email tomorrow and must resolve to one row.
 */
export async function resolveContact(
  db: DbClient,
  input: {
    channel: 'WHATSAPP' | 'WEB';
    /** Phone for WhatsApp, widget session id for web. */
    externalId: string;
    pageUrl?: string | null;
  },
): Promise<ResolvedContact> {
  const kind = input.channel === 'WHATSAPP' ? 'WHATSAPP_PHONE' : 'WEB_VISITOR';

  return db.$transaction(async (tx) => {
    const identity = await tx.contactIdentity.findUnique({
      where: { kind_value: { kind, value: input.externalId } },
      select: { contactId: true },
    });

    let contactId = identity?.contactId ?? null;

    if (!contactId) {
      // refCode is the human-readable support reference. Derived from a hash of
      // the external id so a retried webhook cannot mint a second contact.
      const suffix = createHash('sha256')
        .update(`${kind}:${input.externalId}`)
        .digest('hex')
        .slice(0, 6)
        .toUpperCase();

      const contact = await tx.contact.create({
        data: {
          refCode: `CR-${suffix}`,
          firstChannel: input.channel,
          ...(input.channel === 'WHATSAPP' ? { phoneE164: input.externalId } : {}),
          ...(input.channel === 'WHATSAPP'
            ? { phoneHash: createHash('sha256').update(input.externalId).digest('hex') }
            : {}),
          identities: { create: { kind, value: input.externalId, isPrimary: true } },
        },
        select: { id: true },
      });
      contactId = contact.id;
    }

    const open = await tx.conversation.findFirst({
      where: { contactId, channel: input.channel, externalId: input.externalId, resolved: false },
      orderBy: { startedAt: 'desc' },
      select: { id: true },
    });

    if (open) return { contactId, conversationId: open.id };

    const conversation = await tx.conversation.create({
      data: {
        contactId,
        channel: input.channel,
        externalId: input.externalId,
        pageUrl: input.pageUrl ?? null,
      },
      select: { id: true },
    });

    await tx.contact.update({
      where: { id: contactId },
      data: { conversationCount: { increment: 1 } },
    });

    return { contactId, conversationId: conversation.id };
  });
}
