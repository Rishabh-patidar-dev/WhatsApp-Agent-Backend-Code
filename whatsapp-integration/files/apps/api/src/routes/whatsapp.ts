import { timingSafeEqual } from 'node:crypto';
import { Router } from 'express';
import type { Request, Response } from 'express';
import { JOB_WHATSAPP_REPLY } from '@rag/core';
import {
  isWhatsAppWebhook,
  parseWebhook,
  toReplyJob,
  type StatusUpdate,
} from '@rag/channels';
import { describeError } from '@rag/shared';
import type { DbClient } from '@rag/db';
import type { Logger } from '@rag/shared';
import type { Container, WhatsAppRuntime } from '../container.js';
import { rawJsonBody } from '../middleware/rawBody.js';
import { verifyMetaSignature } from '../middleware/verifyMetaSignature.js';

/**
 * The WhatsApp webhook — the whole public surface of the channel.
 *
 * ── The two-second rule ──────────────────────────────────────────────────
 *
 * Meta expects a 2xx within a few seconds. Miss it and the delivery is marked
 * failed and redelivered, repeatedly, and a webhook that stays slow is
 * eventually unsubscribed altogether. A real answer takes three to five
 * seconds — embed the question, search the cards, verify the price against the
 * database, call the model. Those two facts cannot both be satisfied on this
 * thread.
 *
 * So this route does the least it possibly can: check the signature, read the
 * batch, put one job per message on the queue, answer 200. Everything that
 * thinks happens in `apps/worker`, where taking eight seconds costs nothing.
 *
 * ── Why the 200 comes after the enqueue and not before ───────────────────
 *
 * A queue insert is a millisecond or two; the budget is seconds. Answering
 * first and enqueueing after would mean that when the database is down, Meta
 * hears "got it" and never redelivers — the message is simply lost, silently,
 * exactly when the system is least able to notice. Enqueue first, and a
 * failure becomes a 500, and Meta's redelivery is the retry.
 *
 * ── Idempotency ──────────────────────────────────────────────────────────
 *
 * Meta redelivers anything it believes failed, including deliveries that
 * actually succeeded but answered slowly. `sendUnique` keyed on the WhatsApp
 * message id is the first guard: a duplicate arriving while the first job is
 * still queued enqueues nothing. The second guard is in the worker, where
 * `Message.waMessageId` is a unique column — needed because a duplicate that
 * arrives *after* the first job completed would otherwise pass this one.
 */

export function whatsappRouter(container: Container): Router {
  const router = Router();
  const { logger } = container;
  const whatsapp = container.whatsapp;

  if (!whatsapp) {
    // Not an error. A developer working on retrieval should not have to hold a
    // Meta app to boot the API — but the reason the endpoint 404s should be in
    // the log rather than deduced from its absence.
    logger.warn('WhatsApp channel disabled — WHATSAPP_* config is absent, webhook not mounted');
    return router;
  }

  // ── GET: Meta's one-time subscription handshake ──────────────────────
  //
  // Called once, when the callback URL is saved in the App dashboard, and
  // again whenever it is re-saved. Meta wants the `hub.challenge` echoed back
  // as plain text — as JSON, or with quotes around it, the verification fails
  // with no explanation beyond "The callback URL or verify token couldn't be
  // validated".
  router.get('/webhooks/whatsapp', (req, res) => {
    const mode = String(req.query['hub.mode'] ?? '');
    const token = String(req.query['hub.verify_token'] ?? '');
    const challenge = String(req.query['hub.challenge'] ?? '');

    if (mode === 'subscribe' && safeEqual(token, whatsapp.config.WHATSAPP_VERIFY_TOKEN)) {
      (req.log ?? logger).info('whatsapp webhook verified');
      res.type('text/plain').status(200).send(challenge);
      return;
    }

    (req.log ?? logger).warn('whatsapp webhook verification rejected', { mode });
    res.status(403).type('text/plain').send('Forbidden');
  });

  // ── POST: every inbound message and delivery receipt ─────────────────
  //
  // The parser is mounted here rather than app-wide because the signature is
  // computed over the raw bytes. See rawBody.ts.
  router.post(
    '/webhooks/whatsapp',
    rawJsonBody({ limit: whatsapp.config.WHATSAPP_BODY_LIMIT }),
    verifyMetaSignature(whatsapp.config.WHATSAPP_APP_SECRET),
    (req, res) => {
      void handleWebhook(container, whatsapp, req, res).catch((error: unknown) => {
        (req.log ?? logger).error('whatsapp webhook handler escaped', { error });
        // 500 so Meta redelivers. An escaped error here means the batch was
        // not enqueued, and a lost customer message is worse than a duplicate.
        if (!res.headersSent) res.status(500).json({ status: 'error' });
      });
    },
  );

  return router;
}

async function handleWebhook(
  container: Container,
  whatsapp: WhatsAppRuntime,
  req: Request,
  res: Response,
): Promise<void> {
  const log = req.log ?? container.logger;

  if (!isWhatsAppWebhook(req.body)) {
    // A signed payload from a different Meta product subscribed to the same
    // URL. Acknowledged, so Meta stops redelivering it, and logged, because it
    // means somebody ticked a box in the App dashboard that they should untick.
    log.warn('ignoring non-WhatsApp webhook payload');
    res.status(200).json({ status: 'ignored' });
    return;
  }

  const { messages, statuses } = parseWebhook(req.body);

  // ── inbound messages → jobs ──────────────────────────────────────────
  let enqueued = 0;
  let deduplicated = 0;

  for (const message of messages) {
    const jobId = await container.queue.sendUnique(
      JOB_WHATSAPP_REPLY,
      toReplyJob(message),
      message.waMessageId,
      {
        // Two attempts after the first. A transient Gemini or Anthropic
        // failure is worth retrying; a fourth attempt would mostly be spent
        // answering a question the person has given up on.
        retryLimit: 2,
        retryBackoff: true,
      },
    );

    if (jobId) enqueued += 1;
    else deduplicated += 1;
  }

  // ── delivery receipts → Message.status ───────────────────────────────
  //
  // Not awaited. These are for the dashboard, they outnumber real messages
  // three to one, and none of them is worth spending the response budget on.
  // The failure path is a log line: a lost read receipt costs nothing, and
  // holding Meta's connection open for one costs a redelivery.
  if (statuses.length > 0) {
    void applyStatuses(container.db, statuses, log).catch((error: unknown) => {
      log.warn('failed to apply whatsapp delivery statuses', { reason: describeError(error) });
    });
  }

  // Phone numbers are masked: this line lands in a hosted log viewer that a
  // lot of people can read, and the number is the customer's identity.
  log.info('whatsapp webhook accepted', {
    messages: messages.length,
    enqueued,
    deduplicated,
    statuses: statuses.length,
    phoneNumberId: whatsapp.config.WHATSAPP_PHONE_NUMBER_ID,
    senders: messages.map((message) => maskPhone(message.from)),
  });

  res.status(200).json({ status: 'ok', enqueued });
}

/**
 * How far a message has got, as a number.
 *
 * Receipts arrive out of order often enough to matter — `delivered` before
 * `sent` is routine on a busy number. Without this, a late `sent` callback
 * would move a message that has already been read backwards, and the
 * dashboard would show an answered conversation as pending.
 */
const STATUS_RANK = { PENDING: 0, SENT: 1, DELIVERED: 2, READ: 3, FAILED: 4 } as const;

async function applyStatuses(db: DbClient, statuses: StatusUpdate[], log: Logger): Promise<void> {
  for (const update of statuses) {
    if (update.status === 'FAILED') {
      // Terminal and always applied, whatever the current state: a message
      // that was delivered and then failed is news.
      await db.message.updateMany({
        where: { waMessageId: update.waMessageId },
        data: {
          status: 'FAILED',
          failureReason: [update.errorCode, update.errorTitle].filter(Boolean).join(' — ') || 'unknown',
        },
      });

      log.warn('whatsapp message failed', {
        errorCode: update.errorCode,
        errorTitle: update.errorTitle,
        recipient: maskPhone(update.recipientId),
      });
      continue;
    }

    const lower = (Object.keys(STATUS_RANK) as Array<keyof typeof STATUS_RANK>).filter(
      (name) => STATUS_RANK[name] < STATUS_RANK[update.status],
    );

    // updateMany rather than update: a receipt for a message we have no row for
    // — one sent by an admin from Meta's own inbox, say — must not throw.
    await db.message.updateMany({
      where: { waMessageId: update.waMessageId, status: { in: lower } },
      data: { status: update.status },
    });
  }
}

/** Constant-time, and safe on a length mismatch, which `timingSafeEqual` is not. */
function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a, 'utf8');
  const right = Buffer.from(b, 'utf8');
  return left.length === right.length && timingSafeEqual(left, right);
}

/** `…4821`. Enough to correlate a conversation, not enough to identify a person. */
function maskPhone(phone: string): string {
  return phone.length > 4 ? `…${phone.slice(-4)}` : '…';
}
