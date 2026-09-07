import {
  WHATSAPP_LIMITS,
  clip,
  clipButtons,
  clipSections,
  splitForSend,
  type ListSection,
  type ReplyButton,
} from './format.js';
import { META_ERROR_REENGAGEMENT } from './window.js';

/**
 * Sending to WhatsApp through Meta's Cloud API.
 *
 * ── Why this is a class and not four exported functions ──────────────────
 *
 * It holds the phone number id, the access token and the API version. Those
 * come from validated config, and a module-level singleton reading them at
 * import time is the thing that makes the worker impossible to test without a
 * live Meta app. Constructed once in the worker's `main()`, injected from
 * there, faked in tests by passing `fetchImpl`.
 *
 * ── Retries ──────────────────────────────────────────────────────────────
 *
 * Only on 429 and 5xx, and on a transport failure. A 400 from Meta means the
 * payload was wrong, and sending the same wrong payload twice more turns one
 * clear error into three identical ones a minute apart. The queue is the outer
 * retry loop; this is only the inner one that covers a blip.
 *
 * ── What is never logged ─────────────────────────────────────────────────
 *
 * The payload. It contains the message body, which is the customer's own
 * words — and, once the answer includes a price, the client's commercial
 * terms. Errors carry Meta's response, the status and the recipient's number;
 * they do not carry what was said.
 */

export interface WhatsAppClientOptions {
  /** From the Meta App dashboard — WhatsApp → API Setup. Not the phone number. */
  phoneNumberId: string;
  /** A permanent System User token. Test tokens expire after 24 hours. */
  accessToken: string;
  /** Graph API version, e.g. `v21.0`. Pinned; never "latest". */
  apiVersion: string;
  /** Per attempt, not per call. */
  timeoutMs?: number;
  /** Attempts after the first, for retryable failures only. */
  maxRetries?: number;
  onRetry?: (info: { attemptNumber: number; retriesLeft: number; error: unknown }) => void;
  /** Injected in tests. Defaults to the global `fetch`. */
  fetchImpl?: typeof fetch;
}

export interface SendResult {
  /**
   * Meta's ids for what was sent, in order. Usually one; more when the body
   * had to be split. The first is stored on the assistant `Message` — it is
   * the one delivery receipts arrive for.
   */
  messageIds: string[];
}

export interface TemplateVariable {
  type: 'text';
  text: string;
}

export interface SendTemplateInput {
  /** The template's name in the Meta dashboard, exactly. */
  name: string;
  /** e.g. `es_MX`. Must match the approved template's locale. */
  language: string;
  /** Positional `{{1}}`, `{{2}}` … substitutions for the body. */
  variables?: string[];
}

/**
 * A non-2xx answer from the Graph API, with Meta's own error fields kept.
 *
 * `code` is the number worth branching on: 131047 is the closed window, 131026
 * is an undeliverable recipient, 190 is an expired token. Those three mean
 * three completely different repairs, and the human-readable message does not
 * reliably distinguish them.
 */
export class WhatsAppApiError extends Error {
  override readonly name = 'WhatsAppApiError';

  constructor(
    readonly status: number,
    readonly code: number | null,
    readonly subcode: number | null,
    readonly detail: string,
    readonly retryable: boolean,
  ) {
    super(`WhatsApp Cloud API ${status}${code === null ? '' : ` (code ${code})`}: ${detail}`);
  }

  /** The window closed between the check and the send — a lost race, not a bug. */
  get isWindowExpired(): boolean {
    return this.code === META_ERROR_REENGAGEMENT;
  }

  /** The access token is expired or was revoked. Nothing will send until it is replaced. */
  get isAuthFailure(): boolean {
    return this.status === 401 || this.code === 190;
  }
}

interface GraphResponse {
  messages?: Array<{ id?: string }>;
}

export class WhatsAppClient {
  private readonly endpoint: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly fetchImpl: typeof fetch;

  constructor(private readonly options: WhatsAppClientOptions) {
    this.endpoint = `https://graph.facebook.com/${options.apiVersion}/${options.phoneNumberId}/messages`;
    this.timeoutMs = options.timeoutMs ?? 15_000;
    this.maxRetries = options.maxRetries ?? 2;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
  }

  /**
   * A plain text reply.
   *
   * `previewUrl` is off by default. Meta's link preview fetches the page and
   * renders a card, which for a course page means a screenshot of a price we
   * did not verify sitting under an answer we did.
   */
  async sendText(to: string, body: string, options: { previewUrl?: boolean } = {}): Promise<SendResult> {
    const parts = splitForSend(body);
    if (parts.length === 0) return { messageIds: [] };

    const messageIds: string[] = [];
    // Sequential, not Promise.all: WhatsApp orders messages by arrival, and a
    // parallel send delivers part two before part one often enough to notice.
    for (const part of parts) {
      const id = await this.post({
        messaging_product: 'whatsapp',
        recipient_type: 'individual',
        to,
        type: 'text',
        text: { body: part, preview_url: options.previewUrl ?? false },
      });
      if (id) messageIds.push(id);
    }

    return { messageIds };
  }

  /**
   * An approved template — the only thing that reaches someone outside the
   * 24-hour window. See `window.ts`.
   */
  async sendTemplate(to: string, template: SendTemplateInput): Promise<SendResult> {
    const variables = template.variables ?? [];
    const id = await this.post({
      messaging_product: 'whatsapp',
      recipient_type: 'individual',
      to,
      type: 'template',
      template: {
        name: template.name,
        language: { code: template.language },
        ...(variables.length > 0
          ? {
              components: [
                {
                  type: 'body',
                  parameters: variables.map((text) => ({ type: 'text', text })),
                },
              ],
            }
          : {}),
      },
    });

    return { messageIds: id ? [id] : [] };
  }

  /** Up to three quick-reply buttons under a body. Taps arrive as `interactive`. */
  async sendButtons(
    to: string,
    body: string,
    buttons: readonly ReplyButton[],
    options: { header?: string; footer?: string } = {},
  ): Promise<SendResult> {
    const id = await this.post({
      messaging_product: 'whatsapp',
      recipient_type: 'individual',
      to,
      type: 'interactive',
      interactive: {
        type: 'button',
        ...this.decorations(options),
        body: { text: clip(body, WHATSAPP_LIMITS.INTERACTIVE_BODY) },
        action: {
          buttons: clipButtons(buttons).map((button) => ({
            type: 'reply',
            reply: { id: button.id, title: button.label },
          })),
        },
      },
    });

    return { messageIds: id ? [id] : [] };
  }

  /** A tappable menu. Ten rows total across all sections — `clipSections` enforces it. */
  async sendList(
    to: string,
    body: string,
    buttonLabel: string,
    sections: readonly ListSection[],
    options: { header?: string; footer?: string } = {},
  ): Promise<SendResult> {
    const id = await this.post({
      messaging_product: 'whatsapp',
      recipient_type: 'individual',
      to,
      type: 'interactive',
      interactive: {
        type: 'list',
        ...this.decorations(options),
        body: { text: clip(body, WHATSAPP_LIMITS.INTERACTIVE_BODY) },
        action: {
          button: clip(buttonLabel, WHATSAPP_LIMITS.BUTTON_LABEL),
          sections: clipSections(sections),
        },
      },
    });

    return { messageIds: id ? [id] : [] };
  }

  /**
   * Blue ticks.
   *
   * Worth the extra call: the answer takes three to five seconds, and a read
   * receipt is the only signal in that gap that anything is happening. Failure
   * is swallowed by the caller — a missing tick is cosmetic, and a thrown
   * error here would cost the reply.
   */
  async markRead(waMessageId: string): Promise<void> {
    await this.post({
      messaging_product: 'whatsapp',
      status: 'read',
      message_id: waMessageId,
    });
  }

  private decorations({ header, footer }: { header?: string; footer?: string }): Record<string, unknown> {
    return {
      ...(header ? { header: { type: 'text', text: clip(header, WHATSAPP_LIMITS.HEADER) } } : {}),
      ...(footer ? { footer: { text: clip(footer, WHATSAPP_LIMITS.FOOTER) } } : {}),
    };
  }

  /** One POST with its inner retry loop. Resolves with Meta's message id. */
  private async post(payload: Record<string, unknown>): Promise<string | null> {
    let lastError: unknown;

    for (let attempt = 0; attempt <= this.maxRetries; attempt += 1) {
      if (attempt > 0) {
        this.options.onRetry?.({
          attemptNumber: attempt + 1,
          retriesLeft: this.maxRetries - attempt,
          error: lastError,
        });
        // 300ms, 900ms, … with jitter, so a Meta hiccup does not turn a queue
        // of replies into a synchronised thundering herd on recovery.
        const backoffMs = 300 * 3 ** (attempt - 1);
        await new Promise((resolve) => setTimeout(resolve, backoffMs + Math.random() * 200));
      }

      try {
        return await this.attempt(payload);
      } catch (error) {
        lastError = error;
        const retryable = error instanceof WhatsAppApiError ? error.retryable : true;
        if (!retryable || attempt === this.maxRetries) throw error;
      }
    }

    throw lastError;
  }

  private async attempt(payload: Record<string, unknown>): Promise<string | null> {
    let response: Response;
    try {
      response = await this.fetchImpl(this.endpoint, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${this.options.accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (error) {
      // Transport-level: DNS, TLS, connection reset, or our own timeout. None
      // of them say anything about the payload, so all of them are retryable.
      throw new WhatsAppApiError(
        0,
        null,
        null,
        error instanceof Error ? error.message : String(error),
        true,
      );
    }

    // Read as text first: Meta answers some failures with an HTML error page,
    // and `response.json()` on that throws a parse error that hides the status.
    const raw = await response.text();

    if (!response.ok) {
      const error = extractError(raw);
      throw new WhatsAppApiError(
        response.status,
        error.code,
        error.subcode,
        error.message || raw.slice(0, 400),
        // 429 and 5xx are worth another go; everything else is a bad payload,
        // a bad token, or a recipient who cannot be reached.
        response.status === 429 || response.status >= 500,
      );
    }

    let parsed: GraphResponse;
    try {
      parsed = JSON.parse(raw) as GraphResponse;
    } catch {
      // A 200 we cannot read. The message was almost certainly sent, so this is
      // not retried — a duplicate reply is worse than a missing id.
      return null;
    }

    return parsed.messages?.[0]?.id ?? null;
  }
}

function extractError(raw: string): { code: number | null; subcode: number | null; message: string } {
  try {
    const body = JSON.parse(raw) as {
      error?: { code?: number; error_subcode?: number; message?: string; error_data?: { details?: string } };
    };
    const error = body.error;
    if (!error) return { code: null, subcode: null, message: '' };

    return {
      code: typeof error.code === 'number' ? error.code : null,
      subcode: typeof error.error_subcode === 'number' ? error.error_subcode : null,
      // `error_data.details` is the field that actually names the offending
      // parameter; `message` is usually the generic family description.
      message: [error.message, error.error_data?.details].filter(Boolean).join(' — '),
    };
  } catch {
    return { code: null, subcode: null, message: '' };
  }
}
