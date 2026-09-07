import { fileURLToPath } from 'node:url';
import { JOB_INGEST_DOCUMENT, JOB_WHATSAPP_REPLY, type QueueJob } from '@rag/core';
import type { IngestDocumentJob } from '@rag/core';
import { WhatsAppClient, type WhatsAppReplyJob } from '@rag/channels';
import { createDbClient } from '@rag/db';
import {
  createLogger,
  describeError,
  getAppConfig,
  getDatabaseConfig,
  getEmbeddingConfig,
  getLlmConfig,
  getWhatsAppConfig,
  isWhatsAppConfigured,
} from '@rag/shared';
import {
  AnthropicLlmProvider,
  GoogleEmbeddingProvider,
  PgBossQueue,
  PrismaCardSearch,
  PrismaCardStore,
  PrismaVerification,
} from '@rag/providers';
import { makeIngestDocumentHandler } from './jobs/ingestDocument.js';
import { makeWhatsAppReplyHandler } from './jobs/whatsappReply.js';

/**
 * The queue runtime.
 *
 * ── DIRECT_URL, and why it is worth a paragraph ──────────────────────────
 *
 * pg-boss uses session-level advisory locks, which PgBouncer's transaction
 * pooling mode silently breaks. Direct connection only.
 *
 * Point this at `DATABASE_URL` and jobs enqueue successfully, sit in the table,
 * and are never consumed — no exception, no warning, nothing in any log. The
 * failure looks exactly like "the worker isn't running". `PgBossQueue` refuses a
 * connection string carrying the pooler's fingerprint for that reason.
 *
 * Prisma is the opposite: it wants the pooled endpoint, because the ingest job
 * holds a connection for the length of a transaction and there may be several
 * processes doing so.
 *
 * ── Two connections, on purpose ──────────────────────────────────────────
 *
 *   pg-boss  → DIRECT_URL   (advisory locks, LISTEN/NOTIFY, session state)
 *   Prisma   → DATABASE_URL (pooled, short transactions)
 */

async function main(): Promise<void> {
  const app = getAppConfig();
  const database = getDatabaseConfig();
  const embeddingConfig = getEmbeddingConfig();

  const logger = createLogger({
    name: 'worker',
    level: app.LOG_LEVEL,
    pretty: app.NODE_ENV === 'development',
  });

  const googleKey = embeddingConfig.GOOGLE_API_KEY;
  if (!googleKey) {
    throw new Error(
      `EMBEDDING_PROVIDER is "${embeddingConfig.EMBEDDING_PROVIDER}" and no GOOGLE_API_KEY is set. ` +
        'The ingest job cannot embed a card without it.',
    );
  }

  const db = createDbClient({ connectionString: database.DATABASE_URL });

  const embeddings = new GoogleEmbeddingProvider({
    apiKey: googleKey,
    model: embeddingConfig.EMBEDDING_MODEL,
    dimensions: embeddingConfig.EMBEDDING_DIMENSIONS,
    onRetry: ({ attemptNumber, retriesLeft, error }) =>
      logger.warn('embedding retry', {
        attemptNumber,
        retriesLeft,
        reason: describeError(error),
      }),
  });

  const store = new PrismaCardStore({ db });

  const queue = new PgBossQueue({
    connectionString: database.DIRECT_URL,
    onError: (error) => logger.error('pg-boss error', { error }),
    onWarning: (warning) => logger.warn('pg-boss warning', { warning }),
  });

  await queue.start([JOB_INGEST_DOCUMENT, JOB_WHATSAPP_REPLY]);

  // ── ingest ────────────────────────────────────────────────────────────
  //
  // Concurrency 1 deliberately. A second ingest running alongside the first
  // would race on the same upserts and the same card rows, and the work is
  // minutes of embedding calls — there is nothing to gain by overlapping them.
  await queue.work<IngestDocumentJob>(
    JOB_INGEST_DOCUMENT,
    makeIngestDocumentHandler({ db, embeddings, store, logger }),
    { concurrency: 1 },
  );

  // ── whatsapp ──────────────────────────────────────────────────────────
  //
  // The handler is registered either way. An unregistered queue accepts jobs
  // and never runs them — no error, nothing in any log, and the webhook keeps
  // answering Meta with a cheerful 200 the whole time. Registering a throwing
  // stub instead means an unconfigured deployment sends the message to the
  // dead-letter queue after its retries, where somebody can see it.
  if (isWhatsAppConfigured()) {
    const whatsappConfig = getWhatsAppConfig();

    // The LLM is new to this process — ingest never needed one — and it is
    // read here rather than at the top of main() so that a worker running
    // without a WhatsApp channel still starts with no Anthropic key set.
    // Narrowed rather than asserted, for the same reason as in the API
    // container: the schema's superRefine guarantees the key at runtime but
    // the type stays `string | undefined`, and pointing LLM_PROVIDER at
    // bedrock should fail with a sentence.
    const llmConfig = getLlmConfig();
    const anthropicKey = llmConfig.ANTHROPIC_API_KEY;
    if (!anthropicKey) {
      throw new Error(
        `LLM_PROVIDER is "${llmConfig.LLM_PROVIDER}" and no ANTHROPIC_API_KEY is set. ` +
          'The Bedrock adapter is not implemented yet (packages/providers/src/llm/bedrock.ts).',
      );
    }

    const client = new WhatsAppClient({
      phoneNumberId: whatsappConfig.WHATSAPP_PHONE_NUMBER_ID,
      accessToken: whatsappConfig.WHATSAPP_ACCESS_TOKEN,
      apiVersion: whatsappConfig.WHATSAPP_API_VERSION,
      timeoutMs: whatsappConfig.WHATSAPP_REQUEST_TIMEOUT_SECONDS * 1000,
      onRetry: ({ attemptNumber, retriesLeft, error }) =>
        logger.warn('whatsapp send retry', {
          attemptNumber,
          retriesLeft,
          reason: describeError(error),
        }),
    });

    await queue.work<WhatsAppReplyJob>(
      JOB_WHATSAPP_REPLY,
      makeWhatsAppReplyHandler({
        db,
        client,
        search: new PrismaCardSearch({ db }),
        verification: new PrismaVerification(db),
        embeddings,
        llm: new AnthropicLlmProvider({
          apiKey: anthropicKey,
          model: llmConfig.LLM_MODEL,
          workspaceId: llmConfig.ANTHROPIC_WORKSPACE_ID,
          onRetry: ({ attemptNumber, retriesLeft, error }) =>
            logger.warn('llm retry', { attemptNumber, retriesLeft, reason: describeError(error) }),
        }),
        logger,
        historyTurns: whatsappConfig.WHATSAPP_HISTORY_TURNS,
        maxQueryChars: whatsappConfig.WHATSAPP_MAX_QUERY_CHARS,
        rateLimitPerMinute: whatsappConfig.WHATSAPP_RATE_LIMIT_PER_MINUTE,
      }),
      { concurrency: whatsappConfig.WHATSAPP_WORKER_CONCURRENCY },
    );
  } else {
    logger.warn('WhatsApp channel disabled — WHATSAPP_* config is absent; replies will fail loudly');

    await queue.work<Record<string, unknown>>(
      JOB_WHATSAPP_REPLY,
      async (job: QueueJob<Record<string, unknown>>) => {
        logger.error('whatsapp.reply received but the channel is not configured', { jobId: job.id });
        throw new Error('whatsapp.reply received but WHATSAPP_* config is absent');
      },
      { concurrency: 1 },
    );
  }

  logger.info('worker started', {
    queues: [JOB_INGEST_DOCUMENT, JOB_WHATSAPP_REPLY],
    embedModel: embeddingConfig.EMBEDDING_MODEL,
    whatsapp: isWhatsAppConfigured() ? 'enabled' : 'disabled',
  });

  // ── graceful shutdown ─────────────────────────────────────────────────
  //
  // An ingest mid-embed is worth waiting out: it holds no lock a restart would
  // inherit, and killing it wastes the embedding calls already paid for.
  let stopping = false;
  const shutdown = (signal: string): void => {
    if (stopping) return;
    stopping = true;
    logger.info('shutting down', { signal });

    void (async () => {
      try {
        await queue.stop({ timeoutMs: 30_000 });
        await db.$disconnect();
        logger.info('stopped cleanly');
        process.exit(0);
      } catch (error) {
        logger.error('shutdown failed', { error });
        process.exit(1);
      }
    })();

    setTimeout(() => {
      logger.warn('forcing exit — a job did not finish in time');
      process.exit(1);
    }, 45_000).unref();
  };

  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error: unknown) => {
    process.stderr.write(`worker failed to start: ${describeError(error)}\n`);
    if (error instanceof Error && error.stack) process.stderr.write(`${error.stack}\n`);
    process.exit(1);
  });
}

export { main };
