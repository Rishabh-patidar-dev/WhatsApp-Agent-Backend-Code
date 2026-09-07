import { createDbClient, assertIndexes, type DbClient } from '@rag/db';
import {
  getApiConfig,
  getAppConfig,
  getDatabaseConfig,
  getEmbeddingConfig,
  getLlmConfig,
  getWhatsAppConfig,
  isWhatsAppConfigured,
  createLogger,
  describeError,
  type ApiConfig,
  type Logger,
  type WhatsAppConfig,
} from '@rag/shared';
import { JOB_INGEST_DOCUMENT, JOB_WHATSAPP_REPLY } from '@rag/core';
import type { CardSearchPort, EmbeddingProvider, LlmProvider, QueuePublisher } from '@rag/core';
import type { VerificationPort } from '@rag/core';
import {
  AnthropicLlmProvider,
  GoogleEmbeddingProvider,
  PgBossQueue,
  PrismaCardSearch,
  PrismaVerification,
} from '@rag/providers';

/**
 * Everything the API owns, built once at boot.
 *
 * Providers are constructed here rather than inside a handler because each one
 * holds something a request should not be paying for: `PrismaCardSearch` wraps
 * a connection pool, the Gemini and Anthropic clients keep a keep-alive agent,
 * and pg-boss holds its own pool on a second connection string. Building them
 * per request would open a new pool against the pooled :6543 endpoint on every
 * message — the exact behaviour PgBouncer exists to prevent.
 *
 * The shape is a plain object passed into the routes, not a module-level
 * singleton the routes reach for. That is what makes a route testable with a
 * fake `search` and no database at all.
 *
 * The wiring below is the same wiring proven in `scripts/checkpoint9-answer.ts`.
 * If that script passes and this does not, the difference is here.
 */

/**
 * The WhatsApp channel, when this deployment has one.
 *
 * Only the validated config: the API enqueues, it does not send, so it has no
 * use for a Graph API client. The worker builds one — it is the process that
 * talks to Meta. When an admin "send a manual reply" route lands, this is where
 * its client belongs.
 */
export interface WhatsAppRuntime {
  config: WhatsAppConfig;
}

export interface Container {
  config: ApiConfig;
  logger: Logger;
  db: DbClient;
  embeddings: EmbeddingProvider;
  llm: LlmProvider;
  search: PrismaCardSearch;
  verification: VerificationPort;
  queue: QueuePublisher;
  /** Null when no WHATSAPP_* keys are set — the webhook is then not mounted. */
  whatsapp: WhatsAppRuntime | null;
  /** Set once the startup checks have passed. Read by /health/ready. */
  ready: boolean;
  readinessError: string | null;
  shutdown(): Promise<void>;
}

/** Interface-typed views, so a route cannot reach past the contract. */
export type ContainerSearch = CardSearchPort;

export async function createContainer(): Promise<Container> {
  const app = getAppConfig();
  const config = getApiConfig();
  const database = getDatabaseConfig();
  const embeddingConfig = getEmbeddingConfig();
  const llmConfig = getLlmConfig();

  const logger = createLogger({
    name: 'api',
    level: app.LOG_LEVEL,
    pretty: app.NODE_ENV === 'development',
  });

  // The schema's superRefine guarantees the key is present when
  // LLM_PROVIDER=anthropic, but that is a runtime guarantee and the field stays
  // `string | undefined` on the type. Narrow it rather than assert it away, so
  // pointing LLM_PROVIDER at bedrock fails with a sentence.
  const anthropicKey = llmConfig.ANTHROPIC_API_KEY;
  if (!anthropicKey) {
    throw new Error(
      `LLM_PROVIDER is "${llmConfig.LLM_PROVIDER}" and no ANTHROPIC_API_KEY is set. ` +
        'The Bedrock adapter is not implemented yet (packages/providers/src/llm/bedrock.ts).',
    );
  }
  const googleKey = embeddingConfig.GOOGLE_API_KEY;
  if (!googleKey) {
    throw new Error(
      `EMBEDDING_PROVIDER is "${embeddingConfig.EMBEDDING_PROVIDER}" and no GOOGLE_API_KEY is set.`,
    );
  }

  // Prisma Client on the pooled endpoint.
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

  const llm = new AnthropicLlmProvider({
    apiKey: anthropicKey,
    model: llmConfig.LLM_MODEL,
    workspaceId: llmConfig.ANTHROPIC_WORKSPACE_ID,
    onRetry: ({ attemptNumber, retriesLeft, error }) =>
      logger.warn('llm retry', { attemptNumber, retriesLeft, reason: describeError(error) }),
  });

  const search = new PrismaCardSearch({ db });
  const verification = new PrismaVerification(db);

  // Presence decides whether the webhook is mounted; validity is decided by the
  // schema, which throws a ConfigError naming the missing key. A deployment
  // with three of the four secrets fails at boot rather than serving an
  // endpoint that cannot verify a signature.
  const whatsapp: WhatsAppRuntime | null = isWhatsAppConfigured()
    ? { config: getWhatsAppConfig() }
    : null;

  // pg-boss on DIRECT_URL. The API only publishes — the worker consumes — but
  // it still needs the direct endpoint, for the same advisory-lock reason.
  const queue = new PgBossQueue({
    connectionString: database.DIRECT_URL,
    onError: (error) => logger.error('pg-boss error', { error }),
    onWarning: (warning) => logger.warn('pg-boss warning', { warning }),
  });

  const container: Container = {
    config,
    logger,
    db,
    embeddings,
    llm,
    search,
    verification,
    queue,
    whatsapp,
    ready: false,
    readinessError: null,
    async shutdown() {
      await queue.stop().catch((error: unknown) => {
        logger.warn('queue did not stop cleanly', { error });
      });
      await db.$disconnect().catch((error: unknown) => {
        logger.warn('database did not disconnect cleanly', { error });
      });
    },
  };

  // ── startup checks ───────────────────────────────────────────────────
  //
  // A missing HNSW or trigram index throws nothing at query time: `<=>` quietly
  // falls back to a sequential scan and answers stay correct but slow and
  // worse. Asserted here so a bad deploy fails at boot rather than degrading in
  // production for a week.
  try {
    await assertIndexes(db);

    // Widen the HNSW beam. This is a session-level SET, so through the pooled
    // endpoint it applies to whichever backend connection served it rather than
    // to all of them — worth doing, not worth trusting. If recall matters more
    // than it does today, move the SET into PrismaCardSearch's own query.
    await search.tuneVectorSearch();

    await queue.start([JOB_INGEST_DOCUMENT, JOB_WHATSAPP_REPLY]);

    container.ready = true;
    logger.info('container ready', {
      embedModel: embeddingConfig.EMBEDDING_MODEL,
      embedDims: embeddingConfig.EMBEDDING_DIMENSIONS,
      llmModel: llmConfig.LLM_MODEL,
      allowedOrigins: config.WIDGET_ALLOWED_ORIGINS,
      whatsapp: whatsapp ? whatsapp.config.WHATSAPP_PHONE_NUMBER_ID : 'disabled',
    });
  } catch (error) {
    container.readinessError = describeError(error);
    logger.error('startup checks failed — serving 503 until they pass', { error });
  }

  return container;
}
