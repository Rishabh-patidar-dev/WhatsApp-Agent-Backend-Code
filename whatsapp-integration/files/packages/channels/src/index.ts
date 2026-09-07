// ── web ────────────────────────────────────────────────────────────────
export { SSE_EVENTS, WIDGET_MESSAGES } from './web/protocol.js';
export type {
  ChatRequestBody,
  ChatStatus,
  ErrorFrame,
  HandoffFrame,
  MessageFrame,
  OpenFrame,
  SseEventName,
  StatusFrame,
  WidgetMessage,
  WidgetMessageName,
} from './web/protocol.js';

export { SseStream } from './web/sse.js';
export type { SseSink, SseStreamOptions } from './web/sse.js';
export { WEB_TARGET_CHARS, formatForWeb } from './web/format.js';

// ── whatsapp ───────────────────────────────────────────────────────────
export {
  isWhatsAppWebhook,
  parseWebhook,
  toReplyJob,
} from './whatsapp/parseWebhook.js';
export type {
  DeliveryStatus,
  InboundKind,
  InboundMessage,
  ParsedWebhook,
  StatusUpdate,
  WhatsAppReplyJob,
} from './whatsapp/parseWebhook.js';

export {
  WHATSAPP_LIMITS,
  WHATSAPP_MAX_CHARS,
  WHATSAPP_TARGET_CHARS,
  clip,
  clipButtons,
  clipSections,
  formatForWhatsApp,
  splitForSend,
  toWhatsAppMarkup,
  truncateOnBoundary,
} from './whatsapp/format.js';
export type { ListRow, ListSection, ReplyButton } from './whatsapp/format.js';

export {
  CUSTOMER_SERVICE_WINDOW_MS,
  META_ERROR_REENGAGEMENT,
  WhatsAppWindowClosedError,
  assertWindowOpen,
  formatRemaining,
  windowState,
} from './whatsapp/window.js';
export type { WindowState } from './whatsapp/window.js';

export { WhatsAppApiError, WhatsAppClient } from './whatsapp/client.js';
export type {
  SendResult,
  SendTemplateInput,
  TemplateVariable,
  WhatsAppClientOptions,
} from './whatsapp/client.js';
