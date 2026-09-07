/**
 * Reading Meta's incoming webhook format.
 *
 * Everything that arrives on `POST /webhooks/whatsapp` comes through here, and
 * three properties of that endpoint shape this file:
 *
 *   1. **Deliveries are batched.** One POST can carry several entries, each
 *      with several changes, each with several messages. Meta batches whenever
 *      two messages land close together, so "one webhook, one message" is an
 *      assumption that holds in testing and fails on a busy afternoon.
 *
 *   2. **Delivery receipts share the URL.** `sent`, `delivered`, `read` and
 *      `failed` callbacks arrive on the same endpoint, mixed in with real
 *      messages, and outnumber them roughly three to one. Answering one as if
 *      it were a question is the classic first bug on this integration.
 *
 *   3. **The payload is hostile until proven otherwise.** The signature
 *      middleware proves it came from Meta; it does not prove the shape. So
 *      nothing below indexes into the payload without checking, and anything
 *      unrecognised is skipped rather than thrown over — a new message type
 *      Meta adds next year must not take the webhook down.
 *
 * The job payload lives here too, next to the message it is built from: the
 * API enqueues it and the worker consumes it, and a field that means one thing
 * on each side of that boundary is a bug neither file can see on its own.
 */

/** What the person actually did. Anything else is `unsupported`. */
export type InboundKind = 'text' | 'interactive' | 'button' | 'unsupported';

export interface InboundMessage {
  /** Meta's `wamid...`. The idempotency key for the whole pipeline. */
  waMessageId: string;
  /** Sender's number, digits only, no `+` — the format Meta sends and accepts. */
  from: string;
  /** Which of our numbers received it. Present so a second number is a config change. */
  phoneNumberId: string;
  /** Meta's unix seconds, as a string, exactly as sent. */
  timestamp: string;
  kind: InboundKind;
  /** The question. Empty only for `unsupported`. */
  text: string;
  /** WhatsApp profile name, when the person has one set. */
  profileName: string | null;
  /**
   * Set when the person tapped a list row or a quick-reply button rather than
   * typing. Carries the id we put on that row — see `format.ts` for the limits.
   */
  replyId: string | null;
  /** `image`, `audio`, `document`, `location`, … when `kind` is `unsupported`. */
  mediaType: string | null;
  /** Meta's media id, when there is one. Fetching it is a separate job. */
  mediaId: string | null;
  /** The message this one replies to, when the person used WhatsApp's reply UI. */
  quotedMessageId: string | null;
}

/** Mirrors `MessageStatus` in schema.prisma. PENDING is ours, never Meta's. */
export type DeliveryStatus = 'SENT' | 'DELIVERED' | 'READ' | 'FAILED';

export interface StatusUpdate {
  /** The id of a message *we* sent. Matches `Message.waMessageId`. */
  waMessageId: string;
  status: DeliveryStatus;
  timestamp: string;
  recipientId: string;
  /** Meta's numeric error code on a `failed` callback — 131047, 131026, … */
  errorCode: number | null;
  errorTitle: string | null;
}

export interface ParsedWebhook {
  /** Things a human sent. These become jobs. */
  messages: InboundMessage[];
  /** Receipts for things we sent. These become `Message.status` updates. */
  statuses: StatusUpdate[];
}

/**
 * One `whatsapp.reply` job.
 *
 * A flattened `InboundMessage` plus when we received it. Flat on purpose: this
 * is JSON in a Postgres column that a retry may deserialise hours later, so it
 * holds values, never object references, and nothing that would need the
 * original request to interpret.
 */
export interface WhatsAppReplyJob {
  waMessageId: string;
  from: string;
  phoneNumberId: string;
  kind: InboundKind;
  text: string;
  profileName: string | null;
  replyId: string | null;
  mediaType: string | null;
  /** ISO-8601. When the webhook was accepted, not when Meta says it was sent. */
  receivedAt: string;
}

// ── defensive readers ──────────────────────────────────────────────────
//
// Hand-rolled rather than a schema library: this package's dependencies are
// `@rag/core` and `@rag/shared` and nothing else, and the shape below is
// small enough that a parser is smaller than the dependency would be.

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function strOrNull(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

/** Meta sends error codes as numbers; some historical payloads send strings. */
function numOrNull(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

const STATUS_MAP: Record<string, DeliveryStatus> = {
  sent: 'SENT',
  delivered: 'DELIVERED',
  read: 'READ',
  failed: 'FAILED',
};

/**
 * Every message carrier Meta uses, mapped to a media id.
 *
 * Listed explicitly rather than inferred, because the id lives under a
 * different key for each type and guessing produces a null media id that only
 * shows up when somebody tries to open the attachment.
 */
const MEDIA_TYPES = ['image', 'audio', 'video', 'document', 'sticker'] as const;

/**
 * True when this payload came from the WhatsApp product.
 *
 * The same webhook URL can be subscribed to other Meta products by a
 * mis-click in the App dashboard. Those payloads have a different `object` and
 * parsing them as WhatsApp yields nothing — which is right, but silently.
 */
export function isWhatsAppWebhook(payload: unknown): boolean {
  return str(record(payload)['object']) === 'whatsapp_business_account';
}

export function parseWebhook(payload: unknown): ParsedWebhook {
  const messages: InboundMessage[] = [];
  const statuses: StatusUpdate[] = [];

  for (const entry of array(record(payload)['entry'])) {
    for (const change of array(record(entry)['changes'])) {
      const value = record(record(change)['value']);

      // `messages` is the field for inbound traffic. A change carrying
      // anything else — `message_template_status_update`, `flows` — has no
      // business here and its `value` shape is different.
      const field = str(record(change)['field']);
      if (field !== '' && field !== 'messages') continue;

      const phoneNumberId = str(record(value['metadata'])['phone_number_id']);

      // One `contacts` entry per sender in this change. Indexed by wa_id so a
      // batch from two people does not label both with the first one's name.
      const namesByWaId = new Map<string, string>();
      for (const contact of array(value['contacts'])) {
        const waId = str(record(contact)['wa_id']);
        const name = str(record(record(contact)['profile'])['name']);
        if (waId !== '' && name !== '') namesByWaId.set(waId, name);
      }

      for (const raw of array(value['messages'])) {
        const parsed = parseMessage(record(raw), phoneNumberId, namesByWaId);
        if (parsed) messages.push(parsed);
      }

      for (const raw of array(value['statuses'])) {
        const parsed = parseStatus(record(raw));
        if (parsed) statuses.push(parsed);
      }
    }
  }

  return { messages, statuses };
}

function parseMessage(
  message: Record<string, unknown>,
  phoneNumberId: string,
  namesByWaId: Map<string, string>,
): InboundMessage | null {
  const waMessageId = str(message['id']);
  const from = str(message['from']);
  // No id means no idempotency key and no way to answer; no sender means
  // nowhere to send the answer. Either way there is nothing to do with it.
  if (waMessageId === '' || from === '') return null;

  const base = {
    waMessageId,
    from,
    phoneNumberId,
    timestamp: str(message['timestamp']),
    profileName: namesByWaId.get(from) ?? null,
    quotedMessageId: strOrNull(record(message['context'])['id']),
    mediaType: null as string | null,
    mediaId: null as string | null,
  };

  const type = str(message['type']);

  if (type === 'text') {
    return {
      ...base,
      kind: 'text',
      text: str(record(message['text'])['body']).trim(),
      replyId: null,
    };
  }

  if (type === 'interactive') {
    // A list row and a quick-reply button arrive under different keys with
    // the same {id, title} shape.
    const interactive = record(message['interactive']);
    const reply = record(interactive['list_reply'] ?? interactive['button_reply']);
    const id = strOrNull(reply['id']);
    if (!id) return null;

    return {
      ...base,
      kind: 'interactive',
      // The visible label is what the person believes they said, so it is what
      // goes in the transcript and into the prompt. `replyId` carries the
      // machine-readable half.
      text: str(reply['title']).trim(),
      replyId: id,
    };
  }

  if (type === 'button') {
    // Quick-reply buttons attached to an approved *template* post here rather
    // than under `interactive`, with `payload` instead of `id`.
    const button = record(message['button']);
    return {
      ...base,
      kind: 'button',
      text: str(button['text']).trim(),
      replyId: strOrNull(button['payload']),
    };
  }

  // Everything else — photos, voice notes, PDFs, locations, contacts,
  // reactions, order messages, system notices. Carried through rather than
  // dropped: the person sent something and is waiting, so the worker owes them
  // a sentence saying what it can and cannot read.
  const mediaKey = MEDIA_TYPES.find((candidate) => candidate === type);
  return {
    ...base,
    kind: 'unsupported',
    text: '',
    replyId: null,
    mediaType: type === '' ? 'unknown' : type,
    mediaId: mediaKey ? strOrNull(record(message[mediaKey])['id']) : null,
  };
}

function parseStatus(status: Record<string, unknown>): StatusUpdate | null {
  const waMessageId = str(status['id']);
  const mapped = STATUS_MAP[str(status['status'])];
  if (waMessageId === '' || !mapped) return null;

  // `errors` is an array, but a status carries at most one and only on failure.
  const error = record(array(status['errors'])[0]);

  return {
    waMessageId,
    status: mapped,
    timestamp: str(status['timestamp']),
    recipientId: str(status['recipient_id']),
    errorCode: numOrNull(error['code']),
    errorTitle: strOrNull(error['title']),
  };
}

/** The queue payload for one inbound message. */
export function toReplyJob(message: InboundMessage, receivedAt: Date = new Date()): WhatsAppReplyJob {
  return {
    waMessageId: message.waMessageId,
    from: message.from,
    phoneNumberId: message.phoneNumberId,
    kind: message.kind,
    text: message.text,
    profileName: message.profileName,
    replyId: message.replyId,
    mediaType: message.mediaType,
    receivedAt: receivedAt.toISOString(),
  };
}
