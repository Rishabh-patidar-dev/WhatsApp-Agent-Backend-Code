/**
 * One error vocabulary for everything that faces a caller.
 *
 * The packages below this one already throw well-named domain errors —
 * `RetrievalError`, `VerificationError`, `LlmError`, `MissingIndexError`. Those
 * say what broke. They do not say what a visitor should be told or what status
 * code to answer with, and that judgement should not be made twice in every
 * route.
 *
 * So an `AppError` carries three things a domain error does not:
 *
 *   status        — the HTTP answer
 *   code          — a stable, machine-readable string the widget can branch on
 *   publicMessage — Spanish, safe to render, never containing internals
 *
 * `message` stays for the log. `publicMessage` is the only thing that reaches a
 * browser. Anything that is not an `AppError` is a bug, gets a 500 and a
 * generic Spanish sentence — a stack trace rendered in the chat bubble is both
 * a bad answer and a disclosure.
 */

export type ErrorCode =
  | 'bad_request'
  | 'invalid_api_key'
  | 'invalid_signature'
  | 'origin_not_allowed'
  | 'rate_limited'
  | 'not_found'
  | 'conversation_handed_off'
  | 'upstream_failed'
  | 'not_ready'
  | 'internal';

/** What a caller is allowed to see. Never includes `message`. */
export interface PublicError {
  code: ErrorCode;
  /** Spanish. Rendered directly in the widget. */
  message: string;
  /** Present only when it helps the caller fix the request. */
  details?: Record<string, unknown>;
}

export interface AppErrorOptions {
  /** Spanish text safe to show a visitor. */
  publicMessage?: string;
  /** Attached to the public response — validation paths, retry hints. */
  details?: Record<string, unknown>;
  cause?: unknown;
}

const DEFAULT_PUBLIC_MESSAGE = 'Ocurrió un error inesperado. Vuelve a intentarlo en un momento.';

export class AppError extends Error {
  override readonly name: string = 'AppError';
  readonly status: number;
  readonly code: ErrorCode;
  readonly publicMessage: string;
  readonly details: Record<string, unknown> | undefined;

  constructor(
    status: number,
    code: ErrorCode,
    message: string,
    options: AppErrorOptions = {},
  ) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    this.status = status;
    this.code = code;
    this.publicMessage = options.publicMessage ?? DEFAULT_PUBLIC_MESSAGE;
    this.details = options.details;
  }

  toPublic(): PublicError {
    return {
      code: this.code,
      message: this.publicMessage,
      ...(this.details ? { details: this.details } : {}),
    };
  }
}

export class BadRequestError extends AppError {
  override readonly name = 'BadRequestError';
  constructor(message: string, options: AppErrorOptions = {}) {
    super(400, 'bad_request', message, {
      publicMessage: 'No pudimos leer tu mensaje. Inténtalo de nuevo.',
      ...options,
    });
  }
}

export class InvalidApiKeyError extends AppError {
  override readonly name = 'InvalidApiKeyError';
  constructor(message: string, options: AppErrorOptions = {}) {
    super(401, 'invalid_api_key', message, {
      // Deliberately vague to the visitor: they cannot fix it and the site
      // owner is the one who needs to hear about it.
      publicMessage: 'El chat no está disponible en este sitio.',
      ...options,
    });
  }
}

export class OriginNotAllowedError extends AppError {
  override readonly name = 'OriginNotAllowedError';
  constructor(message: string, options: AppErrorOptions = {}) {
    super(403, 'origin_not_allowed', message, {
      publicMessage: 'El chat no está disponible en este sitio.',
      ...options,
    });
  }
}

/**
 * A webhook whose signature did not verify.
 *
 * Separate from `InvalidApiKeyError` because the two are answered differently
 * and by different people: a bad widget key is a site owner's configuration
 * problem, a bad webhook signature is either a wrong app secret or somebody
 * posting fabricated customer messages at a public URL. Conflating them means
 * the second one is invisible in a log filtered on the first.
 *
 * 403 rather than 401: there is no authentication to retry with. The public
 * message is never rendered anywhere — Meta is the only caller — but it is
 * filled in rather than left to the generic default, because a human hitting
 * this endpoint by hand deserves a sentence that says what went wrong.
 */
export class WebhookSignatureError extends AppError {
  override readonly name = 'WebhookSignatureError';
  constructor(message: string, options: AppErrorOptions = {}) {
    super(403, 'invalid_signature', message, {
      publicMessage: 'La firma del webhook no es válida.',
      ...options,
    });
  }
}

export class NotFoundError extends AppError {
  override readonly name = 'NotFoundError';
  constructor(message: string, options: AppErrorOptions = {}) {
    super(404, 'not_found', message, {
      publicMessage: 'No encontramos lo que buscas.',
      ...options,
    });
  }
}

export class RateLimitedError extends AppError {
  override readonly name = 'RateLimitedError';
  constructor(message: string, options: AppErrorOptions = {}) {
    super(429, 'rate_limited', message, {
      publicMessage: 'Estás enviando mensajes muy rápido. Espera unos segundos.',
      ...options,
    });
  }
}

/**
 * A dependency we do not own failed — Gemini, Anthropic, Postgres. Separated
 * from `internal` because it is the one 5xx that is usually worth retrying.
 */
export class UpstreamError extends AppError {
  override readonly name = 'UpstreamError';
  constructor(message: string, options: AppErrorOptions = {}) {
    super(502, 'upstream_failed', message, {
      publicMessage: 'No pudimos generar la respuesta en este momento. Inténtalo de nuevo.',
      ...options,
    });
  }
}

/** Startup checks have not passed. Load balancers read this, visitors do not. */
export class NotReadyError extends AppError {
  override readonly name = 'NotReadyError';
  constructor(message: string, options: AppErrorOptions = {}) {
    super(503, 'not_ready', message, {
      publicMessage: 'El servicio está iniciando. Inténtalo de nuevo en un momento.',
      ...options,
    });
  }
}

export function isAppError(error: unknown): error is AppError {
  return error instanceof AppError;
}

/**
 * Whatever was thrown, in a shape safe to send.
 *
 * The `unknown` branch is the important one: an unrecognised throw is a bug, so
 * it becomes a 500 with a generic sentence and nothing from the original error.
 * The caller logs the real thing.
 */
export function toPublicError(error: unknown): { status: number; body: PublicError } {
  if (isAppError(error)) return { status: error.status, body: error.toPublic() };

  return {
    status: 500,
    body: { code: 'internal', message: DEFAULT_PUBLIC_MESSAGE },
  };
}

/** Flatten an error chain for a log line — `cause` included, no stack. */
export function describeError(error: unknown): string {
  const parts: string[] = [];
  let current: unknown = error;

  for (let depth = 0; depth < 5 && current !== undefined && current !== null; depth += 1) {
    if (current instanceof Error) {
      parts.push(`${current.name}: ${current.message}`);
      current = current.cause;
    } else {
      parts.push(String(current));
      break;
    }
  }

  return parts.join(' ← ');
}
