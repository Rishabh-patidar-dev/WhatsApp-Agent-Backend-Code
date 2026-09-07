/**
 * WhatsApp's transport limits, in one place.
 *
 * The division of labour here mirrors `web/format.ts`:
 *
 *   `packages/core` owns **presentation** — `formatForWhatsApp` turns the
 *   model's markdown into Meta's four marks and trims to a length a phone
 *   screen reads comfortably. It runs inside `answer()`, and its output is what
 *   gets persisted as the assistant `Message`.
 *
 *   This file owns **transport** — the hard numbers the Graph API enforces.
 *   Meta rejects an entire request when one field is a single character too
 *   long, and it rejects it with a 400 that names the field and not the value,
 *   so a message that is 4097 characters is simply never delivered and nothing
 *   in the log says why. Every string that goes into a payload passes through
 *   `clip` or `splitForSend` first.
 *
 * Re-exporting rather than reimplementing the core half is deliberate: two
 * markdown converters would drift the first time either was corrected.
 */

export {
  WHATSAPP_MAX_CHARS,
  WHATSAPP_TARGET_CHARS,
  formatForWhatsApp,
  toWhatsAppMarkup,
  truncateOnBoundary,
} from '@rag/core';

/**
 * Hard caps from the Cloud API reference. Exceed one and the whole send 400s.
 *
 * `LIST_ROWS_TOTAL` is the one that surprises people: ten is the budget across
 * *all* sections combined, not per section.
 */
export const WHATSAPP_LIMITS = {
  TEXT_BODY: 4096,
  INTERACTIVE_BODY: 1024,
  HEADER: 60,
  FOOTER: 60,
  BUTTON_LABEL: 20,
  BUTTONS_PER_MESSAGE: 3,
  ROW_ID: 200,
  ROW_TITLE: 24,
  ROW_DESCRIPTION: 72,
  SECTION_TITLE: 24,
  LIST_ROWS_TOTAL: 10,
} as const;

/**
 * Fit a single-line field, collapsing whitespace and ending with an ellipsis.
 *
 * Whitespace is collapsed because a newline inside a button label renders as a
 * space on some clients and as nothing on others, and the character still
 * counts against the limit either way.
 */
export function clip(text: string, limit: number): string {
  const collapsed = (text ?? '').split(/\s+/).filter(Boolean).join(' ');
  if (collapsed.length <= limit) return collapsed;
  if (limit <= 1) return collapsed.slice(0, Math.max(0, limit));
  return `${collapsed.slice(0, limit - 1).trimEnd()}…`;
}

/**
 * Split an over-long body into sendable parts, on the largest boundary that
 * fits: paragraph, then line, then word.
 *
 * In the normal path this returns a single part — `formatForWhatsApp` has
 * already trimmed the answer to well under the cap. It exists for the paths
 * that bypass that: an admin's manual reply typed in the dashboard, a canned
 * template, a future channel that hands over raw text. A safety net that never
 * fires is doing its job.
 *
 * Cutting mid-word is the last resort rather than the default because these
 * messages quote prices, and "cuesta $1," is a different number from "$1,850".
 */
export function splitForSend(text: string, limit: number = WHATSAPP_LIMITS.TEXT_BODY): string[] {
  const trimmed = (text ?? '').trim();
  if (trimmed === '') return [];
  if (trimmed.length <= limit) return [trimmed];

  const parts: string[] = [];
  let remaining = trimmed;

  while (remaining.length > limit) {
    const window = remaining.slice(0, limit);
    const cut = Math.max(window.lastIndexOf('\n\n'), window.lastIndexOf('\n'), window.lastIndexOf(' '));
    // A single unbroken run longer than the limit — a URL, usually. Hard cut.
    const at = cut > 0 ? cut : limit;
    const part = remaining.slice(0, at).trim();
    if (part !== '') parts.push(part);
    remaining = remaining.slice(at).trim();
  }

  if (remaining !== '') parts.push(remaining);
  return parts;
}

// ── interactive payload helpers ────────────────────────────────────────
//
// Nothing in the agent sends a menu today: a question is answered by
// retrieval, not by a tree of taps. These exist because the *limits* are the
// hard part of adding one later, and they belong with the other limits rather
// than in whichever file first needs a list. `client.ts` calls them; if no menu
// is ever built, they cost four small functions.

export interface ReplyButton {
  /** Comes back as `InboundMessage.replyId`. Keep it short and stable. */
  id: string;
  label: string;
}

export interface ListRow {
  id: string;
  title: string;
  description?: string;
}

export interface ListSection {
  title: string;
  rows: ListRow[];
}

/** At most three, each label clipped. Meta rejects a fourth outright. */
export function clipButtons(buttons: readonly ReplyButton[]): ReplyButton[] {
  return buttons.slice(0, WHATSAPP_LIMITS.BUTTONS_PER_MESSAGE).map((button) => ({
    id: button.id.slice(0, WHATSAPP_LIMITS.ROW_ID),
    label: clip(button.label, WHATSAPP_LIMITS.BUTTON_LABEL),
  }));
}

/**
 * Fit sections into the ten-row budget, dropping the overflow here rather than
 * letting Meta reject the message. A list that shows nine of eleven courses is
 * a paging problem; a list that fails to send is a broken agent.
 */
export function clipSections(sections: readonly ListSection[]): ListSection[] {
  let budget = WHATSAPP_LIMITS.LIST_ROWS_TOTAL;
  const out: ListSection[] = [];

  for (const section of sections) {
    if (budget <= 0) break;
    const rows = section.rows.slice(0, budget);
    if (rows.length === 0) continue;
    budget -= rows.length;

    out.push({
      title: clip(section.title, WHATSAPP_LIMITS.SECTION_TITLE),
      rows: rows.map((row) => ({
        id: row.id.slice(0, WHATSAPP_LIMITS.ROW_ID),
        title: clip(row.title, WHATSAPP_LIMITS.ROW_TITLE),
        ...(row.description ? { description: clip(row.description, WHATSAPP_LIMITS.ROW_DESCRIPTION) } : {}),
      })),
    });
  }

  return out;
}
