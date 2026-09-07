import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { config as loadDotenv } from 'dotenv';
import { z } from 'zod';

/**
 * The only module in the repo that reads `process.env` (PLAN.md constraint 7).
 * Everything else imports one of the `getXConfig()` accessors below.
 *
 * Config is split into sections and validated lazily, on first access. A
 * scratch script that only embeds a string should not have to supply the
 * WhatsApp secrets to start, and a missing `GOOGLE_API_KEY` should fail where
 * embedding is wired up rather than at import time three packages away.
 */

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

let dotenvLoaded = false;
function ensureDotenv(): void {
  if (dotenvLoaded) return;
  // One .env at the repo root serves every workspace. `override: false` is the
  // default: a real environment variable always beats the file, which is what
  // CI and the container runtime rely on.
  loadDotenv({ path: path.join(repoRoot, '.env'), quiet: true });
  dotenvLoaded = true;
}

/** Trim, then treat an empty string as absent — a blank `KEY=` in .env is not a value. */
const optionalString = z
  .string()
  .transform((v) => v.trim())
  .transform((v) => (v === '' ? undefined : v))
  .optional();

const requiredString = (label: string) =>
  z
    .string({ message: `${label} is required` })
    .transform((v) => v.trim())
    .refine((v) => v !== '', { message: `${label} must not be empty` });

const AppSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  LOG_LEVEL: z.enum(['trace', 'debug', 'info', 'warn', 'error', 'fatal']).default('info'),
});

const DatabaseSchema = z.object({
  /** Pooled :6543 endpoint — Prisma Client at runtime. */
  DATABASE_URL: requiredString('DATABASE_URL'),
  /** Direct :5432 endpoint — Prisma Migrate and pg-boss. Never the pooler. */
  DIRECT_URL: requiredString('DIRECT_URL'),
});

const LlmSchema = z
  .object({
    LLM_PROVIDER: z.enum(['anthropic', 'bedrock']).default('anthropic'),
    LLM_MODEL: requiredString('LLM_MODEL'),
    /** Summarisation, fact extraction, eval grading — anything not user-facing. */
    LLM_MODEL_BACKGROUND: optionalString,
    ANTHROPIC_API_KEY: optionalString,
    /**
     * Required only for an identity-linked API key, which rejects every request
     * without it:
     *   "anthropic-workspace-id is required when authenticating with an
     *    identity-linked API key"
     * A plain org API key does not need it. Console → Settings → Workspaces.
     */
    ANTHROPIC_WORKSPACE_ID: optionalString,
  })
  .transform((v) => ({ ...v, LLM_MODEL_BACKGROUND: v.LLM_MODEL_BACKGROUND ?? v.LLM_MODEL }))
  .superRefine((v, ctx) => {
    if (v.LLM_PROVIDER === 'anthropic' && !v.ANTHROPIC_API_KEY) {
      ctx.addIssue({
        code: 'custom',
        path: ['ANTHROPIC_API_KEY'],
        message: 'ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic',
      });
    }
  });

const EmbeddingSchema = z
  .object({
    EMBEDDING_PROVIDER: z.enum(['google', 'openai', 'bedrock']).default('google'),
    EMBEDDING_MODEL: requiredString('EMBEDDING_MODEL'),
    /**
     * Changing this — or EMBEDDING_MODEL — invalidates every stored vector and
     * forces a full re-embed (RAG_PLAN §6.4). It is not a tuning knob.
     */
    EMBEDDING_DIMENSIONS: z.coerce.number().int().positive().default(1536),
    GOOGLE_API_KEY: optionalString,
    OPENAI_API_KEY: optionalString,
  })
  .superRefine((v, ctx) => {
    if (v.EMBEDDING_PROVIDER === 'google' && !v.GOOGLE_API_KEY) {
      ctx.addIssue({
        code: 'custom',
        path: ['GOOGLE_API_KEY'],
        message: 'GOOGLE_API_KEY is required when EMBEDDING_PROVIDER=google',
      });
    }
    if (v.EMBEDDING_PROVIDER === 'openai' && !v.OPENAI_API_KEY) {
      ctx.addIssue({
        code: 'custom',
        path: ['OPENAI_API_KEY'],
        message: 'OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai',
      });
    }
  });

const StorageSchema = z.object({
  STORAGE_PROVIDER: z.enum(['filesystem', 's3']).default('filesystem'),
  STORAGE_LOCAL_PATH: z.string().default('./.storage'),
  S3_BUCKET: optionalString,
  S3_REGION: optionalString,
});

/**
 * Where the three JUPTR workbooks sit. Defaults point at `data/source/`, whose
 * contents are gitignored — only the `.gitkeep` is committed, because the
 * files carry the client's commercial data.
 */
const IngestionSchema = z.object({
  SOURCE_DIR: z.string().default(path.join(repoRoot, 'data', 'source')),
  SOURCE_COURSES_FILE: z.string().default('JUPTRxRC_Courses.xlsx'),
  SOURCE_PRICING_FILE: z.string().default('JUPTRxRC_Pricing.xlsx'),
  SOURCE_AVAILABILITY_FILE: z.string().default('JUPTR x RC_City_Availability.xlsx'),
});

/**
 * The HTTP surface and the queue runtime.
 *
 * `WIDGET_ALLOWED_ORIGINS` is the only one of these that is load-bearing for
 * security. The widget key travels inside public WordPress HTML, so anybody can
 * read it; the origin list is what stops another site pasting it into their own
 * page and spending the Gemini and Anthropic budget. An empty list would allow
 * everything, so an empty list is rejected.
 */
const ApiSchema = z
  .object({
  API_PORT: z.coerce.number().int().positive().max(65535).default(3001),
  API_HOST: z.string().default('0.0.0.0'),

  /**
   * The origin this API is reached at from a browser — e.g.
   * `https://api.cruzroja.example`. Not cosmetic, and not derivable from
   * `Host`, which the client controls.
   *
   * The widget's iframe is served from `/widget/frame`, so the `fetch('/chat')`
   * inside it is same-origin — and browsers still send `Origin` on a same-origin
   * POST. That header carries *this* value, not the WordPress site's. Without it
   * in the allow-list every widget request is a 403, which is exactly how the
   * widget failed the first time it was wired up.
   *
   * Allowing it is safe because the frame that produces it is itself gated: the
   * document is served with a per-key `frame-ancestors` policy, so a browser
   * refuses to render it inside a site the key was not issued for.
   */
  API_PUBLIC_ORIGIN: optionalString,

  WIDGET_ALLOWED_ORIGINS: z
    .string({ message: 'WIDGET_ALLOWED_ORIGINS is required' })
    .transform((v) =>
      v
        .split(',')
        .map((origin) => origin.trim().replace(/\/$/, ''))
        .filter((origin) => origin !== ''),
    )
    .refine((origins) => origins.length > 0, {
      message: 'WIDGET_ALLOWED_ORIGINS must list at least one origin — an empty list allows none',
    }),

  /** Chat turns per minute, per widget key and IP. Each turn costs two API calls. */
  CHAT_RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().default(12),
  /** Prior turns replayed into the prompt. Six is three exchanges. */
  CHAT_HISTORY_TURNS: z.coerce.number().int().nonnegative().max(40).default(6),
  /** Largest accepted JSON body. A question is a few hundred bytes. */
  API_BODY_LIMIT: z.string().default('32kb'),
  /**
   * Seconds a chat request may run before the SSE stream is closed with an
   * error. Above the p99 of retrieve + verify + generate, below the point where
   * a visitor has given up.
   */
  CHAT_TIMEOUT_SECONDS: z.coerce.number().int().positive().default(45),
  })
  .transform((value) => ({
    ...value,
    // Defaults to the local dev origin, so nothing extra is needed to run the
    // widget end to end on a laptop.
    API_PUBLIC_ORIGIN: (value.API_PUBLIC_ORIGIN ?? `http://localhost:${value.API_PORT}`).replace(
      /\/$/,
      '',
    ),
  }));

/**
 * Meta's WhatsApp Cloud API.
 *
 * Validated lazily like every other section, and — unlike the others — the
 * channel it configures is optional. A developer working on retrieval should
 * be able to boot the API without holding a Meta app, so `isWhatsAppConfigured`
 * decides whether the webhook is mounted at all and this schema decides whether
 * the values are usable once it is.
 *
 * ── WHATSAPP_APP_SECRET is required, not optional ────────────────────────
 *
 * The tempting alternative is to skip signature verification when the secret is
 * absent, so local testing is easy. That leaves a public URL that anyone can
 * POST a fabricated customer message to — one that gets answered, billed, and
 * written into a real contact's history. The convenient version is not offered:
 * `npm run checkpoint:10` signs its own test payloads, which is what the
 * convenience was for.
 */
const WhatsAppSchema = z.object({
  /** WhatsApp → API Setup in the App dashboard. Not the phone number itself. */
  WHATSAPP_PHONE_NUMBER_ID: requiredString('WHATSAPP_PHONE_NUMBER_ID'),
  /** A permanent System User token. The dashboard's test token expires in 24h. */
  WHATSAPP_ACCESS_TOKEN: requiredString('WHATSAPP_ACCESS_TOKEN'),
  /** Our own string, echoed back to Meta during the GET handshake. */
  WHATSAPP_VERIFY_TOKEN: requiredString('WHATSAPP_VERIFY_TOKEN'),
  /** App → Settings → Basic. Signs every delivery; see verifyMetaSignature.ts. */
  WHATSAPP_APP_SECRET: requiredString('WHATSAPP_APP_SECRET'),
  /**
   * Pinned, never "latest". Meta retires a version roughly two years after
   * release and a retired one starts answering 400 with no warning, so this is
   * a value somebody bumps deliberately after reading the changelog.
   */
  WHATSAPP_API_VERSION: z.string().default('v21.0'),
  /** Prior messages replayed into the prompt. Six is three exchanges. */
  WHATSAPP_HISTORY_TURNS: z.coerce.number().int().nonnegative().max(40).default(6),
  /** Longer questions are truncated, never rejected. */
  WHATSAPP_MAX_QUERY_CHARS: z.coerce.number().int().positive().default(1000),
  /** Turns per minute per phone number. Each is one embedding + one model call. */
  WHATSAPP_RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().default(12),
  /** Per Graph API request attempt. */
  WHATSAPP_REQUEST_TIMEOUT_SECONDS: z.coerce.number().int().positive().default(15),
  /** Largest webhook body accepted. Meta's batches are small. */
  WHATSAPP_BODY_LIMIT: z.string().default('1mb'),
  /**
   * Replies generated at once by one worker process.
   *
   * pg-boss gives no per-recipient ordering guarantee, so above 1 two messages
   * sent seconds apart by the same person can be answered out of order. Three
   * is the trade made here: a turn takes three to five seconds, and at 1 a
   * single slow question queues everybody behind it. Set it to 1 for strict
   * ordering, or partition by phone number when that stops being enough.
   */
  WHATSAPP_WORKER_CONCURRENCY: z.coerce.number().int().positive().max(50).default(3),
});

export type AppConfig = z.infer<typeof AppSchema>;
export type ApiConfig = z.infer<typeof ApiSchema>;
export type DatabaseConfig = z.infer<typeof DatabaseSchema>;
export type LlmConfig = z.infer<typeof LlmSchema>;
export type EmbeddingConfig = z.infer<typeof EmbeddingSchema>;
export type StorageConfig = z.infer<typeof StorageSchema>;
export type WhatsAppConfig = z.infer<typeof WhatsAppSchema>;

export interface IngestionConfig {
  coursesPath: string;
  pricingPath: string;
  availabilityPath: string;
}

export class ConfigError extends Error {
  override readonly name = 'ConfigError';
  constructor(section: string, issues: readonly z.core.$ZodIssue[]) {
    const detail = issues.map((i) => `  ${i.path.join('.') || '(root)'}: ${i.message}`).join('\n');
    super(`Invalid ${section} configuration:\n${detail}`);
  }
}

const cache = new Map<string, unknown>();

function section<T>(name: string, schema: z.ZodType<T>): T {
  const hit = cache.get(name);
  if (hit !== undefined) return hit as T;

  ensureDotenv();
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) throw new ConfigError(name, parsed.error.issues);

  cache.set(name, parsed.data);
  return parsed.data;
}

export const getAppConfig = (): AppConfig => section('app', AppSchema);
export const getApiConfig = (): ApiConfig => section('api', ApiSchema);
export const getDatabaseConfig = (): DatabaseConfig => section('database', DatabaseSchema);
export const getLlmConfig = (): LlmConfig => section('llm', LlmSchema);
export const getEmbeddingConfig = (): EmbeddingConfig => section('embedding', EmbeddingSchema);
export const getStorageConfig = (): StorageConfig => section('storage', StorageSchema);
export const getWhatsAppConfig = (): WhatsAppConfig => section('whatsapp', WhatsAppSchema);

/**
 * Whether this deployment has a WhatsApp channel at all.
 *
 * Presence, not validity: `getWhatsAppConfig()` is what says whether the values
 * are usable, and it throws a ConfigError naming the missing key when they are
 * not. This is only the question the API asks before deciding to mount the
 * webhook, so that a half-configured deployment fails loudly rather than
 * serving a webhook that cannot verify a signature.
 *
 * The four keys below are the ones with no default. If any is present, the
 * operator intended a WhatsApp channel and a missing sibling is a mistake worth
 * a startup error rather than a silently disabled endpoint.
 */
export function isWhatsAppConfigured(): boolean {
  ensureDotenv();
  return [
    'WHATSAPP_PHONE_NUMBER_ID',
    'WHATSAPP_ACCESS_TOKEN',
    'WHATSAPP_VERIFY_TOKEN',
    'WHATSAPP_APP_SECRET',
  ].some((key) => (process.env[key] ?? '').trim() !== '');
}

export function getIngestionConfig(): IngestionConfig {
  const raw = section('ingestion', IngestionSchema);
  return {
    coursesPath: path.resolve(raw.SOURCE_DIR, raw.SOURCE_COURSES_FILE),
    pricingPath: path.resolve(raw.SOURCE_DIR, raw.SOURCE_PRICING_FILE),
    availabilityPath: path.resolve(raw.SOURCE_DIR, raw.SOURCE_AVAILABILITY_FILE),
  };
}

/** Test-only. Lets a suite mutate process.env between cases. */
export function resetConfigCache(): void {
  cache.clear();
  dotenvLoaded = false;
}

// TODO (dashboard): the NextAuth section. The keys already exist in
// .env.example; they are not validated here yet because nothing reads them.
// Add them as `getAuthConfig()` following the pattern above — lazily, so a
// script that only embeds a string still starts without them present.
//
// The WhatsApp section above is the worked example of that pattern.
