import type { Logger } from '@rag/shared';

/**
 * Request-scoped state, attached by middleware and read by routes.
 *
 * Declaration merging rather than `res.locals`: `locals` is typed
 * `Record<string, any>`, so a typo in a route would compile and fail at
 * runtime — on the one code path (the widget key) where getting it wrong means
 * serving an unauthenticated request.
 */
declare global {
  namespace Express {
    interface Request {
      /** Set by requestContext. Correlates every log line for this request. */
      requestId?: string;
      /** Child logger bound to requestId. Prefer this over container.logger. */
      log?: Logger;
      /**
       * Set by rawJsonBody on the WhatsApp webhook only. The exact bytes Meta
       * sent, kept because the signature is an HMAC over them and a
       * re-serialised body produces a different digest.
       */
      rawBody?: Buffer;
      /** Set by requireWidgetKey once the key and origin have both passed. */
      widgetKey?: {
        id: string;
        label: string;
      };
    }
  }
}

export {};
