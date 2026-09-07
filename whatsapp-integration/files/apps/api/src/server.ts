import { fileURLToPath } from 'node:url';
import express, { type Express } from 'express';
import cors from 'cors';
import helmet from 'helmet';
import { OriginNotAllowedError, describeError } from '@rag/shared';
import { createContainer, type Container } from './container.js';
import { createWidgetKeyMiddleware } from './middleware/apiKey.js';
import { errorHandler, notFoundHandler, requestContext } from './middleware/errorHandler.js';
import { chatRouter } from './routes/chat.js';
import { healthRouter } from './routes/health.js';
import { whatsappRouter } from './routes/whatsapp.js';
import { widgetRouter } from './routes/widget.js';

/**
 * The HTTP surface.
 *
 * ── Why CORS is not a formality here ─────────────────────────────────────
 *
 * The widget runs in an iframe on the client's WordPress domain, so every call
 * is cross-origin by construction. `WIDGET_ALLOWED_ORIGINS` is the browser-side
 * half of the control; `WidgetKey.allowedOrigins` is the server-side half, per
 * key. Both exist because they fail differently: the CORS list is a deploy-wide
 * setting an operator edits, the key list is per customer and editable from the
 * dashboard without a restart.
 *
 * The reflected-origin shortcut (`origin: true`) would defeat both. It is not
 * used.
 */

export function createApp(container: Container): Express {
  const app = express();
  const { config, logger } = container;

  // Behind a proxy, req.ip must come from X-Forwarded-For or the rate limiter
  // keys every visitor to the load balancer's address. `1` — trust exactly one
  // hop; trusting all of them lets a client spoof the header.
  app.set('trust proxy', 1);
  app.disable('x-powered-by');

  app.use(
    helmet({
      // No HTML is served from here, and the CSP default would otherwise apply
      // to JSON responses for no benefit.
      contentSecurityPolicy: false,
      crossOriginResourcePolicy: { policy: 'cross-origin' },
    }),
  );

  // Before CORS, so a rejected origin still gets a request id in its response
  // and a matching log line. Debugging a CORS failure without one means
  // guessing which of the day's requests was yours.
  app.use(requestContext(logger));

  // The API's own origin belongs in the allow-list: the widget's iframe is
  // served from here, and the fetch it makes to /chat is same-origin — which
  // browsers still stamp with an Origin header on a POST.
  const allowedOrigins = [...config.WIDGET_ALLOWED_ORIGINS, config.API_PUBLIC_ORIGIN];
  const allowed = new Set(allowedOrigins.map((o) => o.toLowerCase()));
  app.use(
    cors({
      origin: (origin, callback) => {
        // No Origin: curl, a health probe, a server-to-server call. Allowed
        // through CORS — which is a browser mechanism and says nothing here —
        // and refused later by the widget-key middleware on /chat.
        if (!origin) return callback(null, true);

        const candidate = origin.trim().replace(/\/$/, '').toLowerCase();
        if (allowed.has(candidate)) return callback(null, true);

        callback(new OriginNotAllowedError(`Origin ${candidate} is not in WIDGET_ALLOWED_ORIGINS`));
      },
      credentials: false,
      allowedHeaders: ['Content-Type', 'X-Widget-Key', 'X-Request-Id'],
      exposedHeaders: ['X-Request-Id'],
      maxAge: 86_400,
    }),
  );

  // ── WhatsApp webhook — mounted HERE, before express.json ───────────────
  //
  // The Meta signature is an HMAC over the *raw* request body. Once
  // express.json has parsed and re-serialised it the bytes differ and the
  // signature never matches — the single most common integration bug on this
  // endpoint, and one that presents as "our app secret is wrong". So the
  // router brings its own `express.json({ verify })` (see middleware/rawBody.ts)
  // and must be mounted above the global parser, never under it.
  //
  // It is also outside CORS and the widget key by design: the caller is Meta's
  // server, not a browser, and it authenticates with a signature instead.
  app.use(whatsappRouter(container));

  app.use(express.json({ limit: config.API_BODY_LIMIT }));

  // Unauthenticated: a readiness probe has no widget key and should not need one.
  app.use(healthRouter(container));

  // The widget's own assets. `/widget/frame` does its own key lookup because it
  // needs the key's origin list to build a frame-ancestors policy — the widget
  // key middleware below cannot help, since a browser loading an iframe sends
  // no X-Widget-Key header and no Origin.
  app.use(widgetRouter(container));

  // Scoped to the path, not mounted globally: an unmatched route must reach
  // notFoundHandler and answer 404. Mounted app-wide, every typo'd path would
  // answer "invalid widget key" instead and send whoever is debugging it after
  // the wrong problem.
  app.use(
    '/chat',
    createWidgetKeyMiddleware({
      db: container.db,
      globalAllowedOrigins: allowedOrigins,
    }),
  );
  app.use(chatRouter(container));

  app.use(notFoundHandler);
  app.use(errorHandler(logger));

  return app;
}

export async function start(): Promise<void> {
  const container = await createContainer();
  const app = createApp(container);
  const { config, logger } = container;

  const server = app.listen(config.API_PORT, config.API_HOST, () => {
    logger.info('api listening', {
      host: config.API_HOST,
      port: config.API_PORT,
      ready: container.ready,
    });
  });

  // Keep-alive slightly above a typical load balancer's 60s idle timeout, so
  // the balancer closes idle connections rather than racing us to it.
  server.keepAliveTimeout = 65_000;
  server.headersTimeout = 66_000;
  // An SSE turn holds the socket for the length of a generation. The default
  // request timeout would cut a slow one off mid-answer.
  server.requestTimeout = (config.CHAT_TIMEOUT_SECONDS + 15) * 1000;

  let shuttingDown = false;
  const shutdown = (signal: string): void => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info('shutting down', { signal });

    server.close(() => {
      void container.shutdown().then(() => process.exit(0));
    });

    // In-flight SSE streams can hold the server open past any sane wait.
    setTimeout(() => {
      logger.warn('forcing exit — connections still open');
      process.exit(1);
    }, 20_000).unref();
  };

  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

// Started directly (`npm start -w @rag/api`), not imported by a test.
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  start().catch((error: unknown) => {
    process.stderr.write(`api failed to start: ${describeError(error)}\n`);
    if (error instanceof Error && error.stack) process.stderr.write(`${error.stack}\n`);
    process.exit(1);
  });
}
