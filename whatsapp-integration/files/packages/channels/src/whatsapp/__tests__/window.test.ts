import { describe, expect, it } from 'vitest';
import {
  CUSTOMER_SERVICE_WINDOW_MS,
  WhatsAppWindowClosedError,
  assertWindowOpen,
  formatRemaining,
  windowState,
} from '../window.js';

const NOW = new Date('2026-03-01T12:00:00.000Z');
const ago = (ms: number) => new Date(NOW.getTime() - ms);

describe('windowState', () => {
  it('is open just inside twenty-four hours', () => {
    const state = windowState(ago(CUSTOMER_SERVICE_WINDOW_MS - 60_000), NOW);

    expect(state.open).toBe(true);
    expect(state.requiresTemplate).toBe(false);
    expect(state.msRemaining).toBe(60_000);
  });

  it('is closed just outside it', () => {
    const state = windowState(ago(CUSTOMER_SERVICE_WINDOW_MS + 1), NOW);

    expect(state.open).toBe(false);
    expect(state.requiresTemplate).toBe(true);
    expect(state.msRemaining).toBe(0);
  });

  it('reports when it closes, measured from the last inbound message', () => {
    const last = ago(60 * 60 * 1000);
    expect(windowState(last, NOW).expiresAt?.toISOString()).toBe(
      new Date(last.getTime() + CUSTOMER_SERVICE_WINDOW_MS).toISOString(),
    );
  });

  it('accepts an ISO string, which is how it arrives on a job payload', () => {
    expect(windowState(ago(1000).toISOString(), NOW).open).toBe(true);
  });

  it('treats “never wrote to us” and “unparseable date” as closed', () => {
    for (const value of [null, undefined, 'not a date']) {
      const state = windowState(value, NOW);
      expect(state.open).toBe(false);
      expect(state.expiresAt).toBeNull();
    }
  });
});

describe('assertWindowOpen', () => {
  it('returns the state while the window is open', () => {
    expect(assertWindowOpen('5215598765432', ago(1000), NOW).open).toBe(true);
  });

  it('throws with the expiry, so a log can say by how much it was missed', () => {
    try {
      assertWindowOpen('5215598765432', ago(CUSTOMER_SERVICE_WINDOW_MS + 3_600_000), NOW);
      expect.unreachable('should have thrown');
    } catch (error) {
      expect(error).toBeInstanceOf(WhatsAppWindowClosedError);
      expect((error as WhatsAppWindowClosedError).state.expiresAt).toEqual(ago(3_600_000));
    }
  });
});

describe('formatRemaining', () => {
  it('reads as a countdown an admin can act on', () => {
    expect(formatRemaining(0)).toBe('cerrada');
    expect(formatRemaining(-5)).toBe('cerrada');
    expect(formatRemaining(90_000)).toBe('1 min');
    expect(formatRemaining(3 * 3_600_000 + 12 * 60_000)).toBe('3 h 12 min');
  });
});
