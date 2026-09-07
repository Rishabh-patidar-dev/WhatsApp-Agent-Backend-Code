/**
 * The 24-hour customer service window.
 *
 * WhatsApp is not email. Once a person messages the business, Meta opens a
 * 24-hour window during which any free-form reply is allowed. When it closes,
 * free-form sends are refused — error 131047, "Re-engagement message" — and the
 * only way to reach that person again is an approved template.
 *
 * That constraint reaches three places, which is why the arithmetic is here
 * and not in any of them:
 *
 *   the worker      — checks before sending a generated answer
 *   the dashboard   — shows an admin how long they have to take over
 *   the API         — refuses a manual send that would bounce
 *
 * ── Why this is checked even when answering an inbound message ───────────
 *
 * It looks redundant: the window opens on the message being answered, so it is
 * open by construction. It is not. A job that failed twice and is on its third
 * pg-boss retry, or one that sat behind a long queue after an outage, can run
 * well after the message arrived. Sending then throws a Graph API error that
 * reads like an auth failure. Checking first turns that into one clear log
 * line and a `Message` row an admin can act on.
 *
 * The window is measured from the *last* inbound message, not the first: every
 * message the person sends restarts it.
 */

export const CUSTOMER_SERVICE_WINDOW_MS = 24 * 60 * 60 * 1000;

/** Meta's code for a free-form send after the window closed. */
export const META_ERROR_REENGAGEMENT = 131047;

export interface WindowState {
  /** A free-form message may be sent right now. */
  open: boolean;
  /** When it closes, or null if the person has never written to us. */
  expiresAt: Date | null;
  /** Never negative. Zero when closed. */
  msRemaining: number;
  /** The inverse of `open`, named for the decision it drives. */
  requiresTemplate: boolean;
}

export function windowState(
  lastInboundAt: Date | string | null | undefined,
  now: Date = new Date(),
): WindowState {
  const last = lastInboundAt == null ? null : new Date(lastInboundAt);

  // No inbound message ever, or a date that did not parse. Both mean the same
  // thing operationally — we cannot prove the window is open, so it is closed.
  if (!last || Number.isNaN(last.getTime())) {
    return { open: false, expiresAt: null, msRemaining: 0, requiresTemplate: true };
  }

  const expiresAt = new Date(last.getTime() + CUSTOMER_SERVICE_WINDOW_MS);
  const msRemaining = Math.max(0, expiresAt.getTime() - now.getTime());

  return {
    open: msRemaining > 0,
    expiresAt,
    msRemaining,
    requiresTemplate: msRemaining <= 0,
  };
}

/**
 * Thrown instead of letting the Graph API refuse the send.
 *
 * Carries the state so the caller can log how long ago it closed — the
 * difference between "by four minutes" (a slow queue worth fixing) and "by
 * three days" (a stale job that should be discarded) is the whole diagnosis.
 */
export class WhatsAppWindowClosedError extends Error {
  override readonly name = 'WhatsAppWindowClosedError';
  constructor(
    readonly to: string,
    readonly state: WindowState,
  ) {
    super(
      `The 24-hour customer service window for ${to} is closed` +
        (state.expiresAt ? ` (expired ${state.expiresAt.toISOString()})` : ' (no inbound message on record)') +
        '. Only an approved template may be sent.',
    );
  }
}

export function assertWindowOpen(
  to: string,
  lastInboundAt: Date | string | null | undefined,
  now: Date = new Date(),
): WindowState {
  const state = windowState(lastInboundAt, now);
  if (!state.open) throw new WhatsAppWindowClosedError(to, state);
  return state;
}

/**
 * "23 h 41 min" — for the dashboard's countdown.
 *
 * Deliberately coarse. An admin deciding whether to take a conversation over
 * needs to know it is hours rather than minutes; a ticking seconds display
 * would just be a reason to re-render the page every second.
 */
export function formatRemaining(msRemaining: number): string {
  if (msRemaining <= 0) return 'cerrada';

  const totalMinutes = Math.floor(msRemaining / 60_000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours === 0) return `${minutes} min`;
  return `${hours} h ${minutes} min`;
}
