import { describe, expect, it } from 'vitest';
import { isWhatsAppWebhook, parseWebhook, toReplyJob } from '../parseWebhook.js';

/**
 * The fixtures below are trimmed copies of real Cloud API deliveries. The
 * shapes matter more than the values: every bug this file has ever caught was
 * a key that lives somewhere other than where it looks like it should.
 */

function envelope(value: Record<string, unknown>, field = 'messages'): unknown {
  return {
    object: 'whatsapp_business_account',
    entry: [{ id: '102290129340398', changes: [{ field, value }] }],
  };
}

const METADATA = { display_phone_number: '5215512345678', phone_number_id: '106540352242922' };

describe('parseWebhook', () => {
  it('reads a text message and the sender profile name', () => {
    const { messages, statuses } = parseWebhook(
      envelope({
        messaging_product: 'whatsapp',
        metadata: METADATA,
        contacts: [{ profile: { name: 'María Fernández' }, wa_id: '5215598765432' }],
        messages: [
          {
            from: '5215598765432',
            id: 'wamid.HBgLNT',
            timestamp: '1770000000',
            type: 'text',
            text: { body: '  ¿Cuánto cuesta el curso de primeros auxilios?  ' },
          },
        ],
      }),
    );

    expect(statuses).toEqual([]);
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      waMessageId: 'wamid.HBgLNT',
      from: '5215598765432',
      phoneNumberId: '106540352242922',
      kind: 'text',
      text: '¿Cuánto cuesta el curso de primeros auxilios?',
      profileName: 'María Fernández',
      replyId: null,
    });
  });

  it('matches each profile name to its own sender in a batched delivery', () => {
    const { messages } = parseWebhook(
      envelope({
        metadata: METADATA,
        contacts: [
          { profile: { name: 'Ana' }, wa_id: '5211111111111' },
          { profile: { name: 'Beto' }, wa_id: '5212222222222' },
        ],
        messages: [
          { from: '5212222222222', id: 'wamid.B', timestamp: '2', type: 'text', text: { body: 'dos' } },
          { from: '5211111111111', id: 'wamid.A', timestamp: '1', type: 'text', text: { body: 'uno' } },
        ],
      }),
    );

    expect(messages.map((m) => [m.from, m.profileName])).toEqual([
      ['5212222222222', 'Beto'],
      ['5211111111111', 'Ana'],
    ]);
  });

  it('separates delivery receipts from real messages', () => {
    const { messages, statuses } = parseWebhook(
      envelope({
        metadata: METADATA,
        statuses: [
          {
            id: 'wamid.OUT1',
            status: 'read',
            timestamp: '1770000100',
            recipient_id: '5215598765432',
          },
        ],
      }),
    );

    expect(messages).toEqual([]);
    expect(statuses).toEqual([
      {
        waMessageId: 'wamid.OUT1',
        status: 'READ',
        timestamp: '1770000100',
        recipientId: '5215598765432',
        errorCode: null,
        errorTitle: null,
      },
    ]);
  });

  it('keeps the error code from a failed status', () => {
    const { statuses } = parseWebhook(
      envelope({
        metadata: METADATA,
        statuses: [
          {
            id: 'wamid.OUT2',
            status: 'failed',
            timestamp: '1770000200',
            recipient_id: '5215598765432',
            errors: [{ code: 131047, title: 'Re-engagement message' }],
          },
        ],
      }),
    );

    expect(statuses[0]).toMatchObject({ status: 'FAILED', errorCode: 131047 });
  });

  it('reads a list-row tap as text plus the row id', () => {
    const { messages } = parseWebhook(
      envelope({
        metadata: METADATA,
        messages: [
          {
            from: '5215598765432',
            id: 'wamid.LIST',
            timestamp: '3',
            type: 'interactive',
            interactive: {
              type: 'list_reply',
              list_reply: { id: 'crs:HP038', title: 'RCP y DEA', description: '8 h' },
            },
          },
        ],
      }),
    );

    expect(messages[0]).toMatchObject({ kind: 'interactive', text: 'RCP y DEA', replyId: 'crs:HP038' });
  });

  it('reads a quick-reply button tap', () => {
    const { messages } = parseWebhook(
      envelope({
        metadata: METADATA,
        messages: [
          {
            from: '5215598765432',
            id: 'wamid.BTN',
            timestamp: '4',
            type: 'interactive',
            interactive: { type: 'button_reply', button_reply: { id: 'act:enroll', title: 'Inscribirme' } },
          },
        ],
      }),
    );

    expect(messages[0]).toMatchObject({ kind: 'interactive', replyId: 'act:enroll' });
  });

  it('reads a template button, which uses `payload` rather than `id`', () => {
    const { messages } = parseWebhook(
      envelope({
        metadata: METADATA,
        messages: [
          {
            from: '5215598765432',
            id: 'wamid.TPL',
            timestamp: '5',
            type: 'button',
            button: { text: 'Sí, continuar', payload: 'resume:1' },
          },
        ],
      }),
    );

    expect(messages[0]).toMatchObject({ kind: 'button', text: 'Sí, continuar', replyId: 'resume:1' });
  });

  it('carries an image through as unsupported, with its media id', () => {
    const { messages } = parseWebhook(
      envelope({
        metadata: METADATA,
        messages: [
          {
            from: '5215598765432',
            id: 'wamid.IMG',
            timestamp: '6',
            type: 'image',
            image: { id: '1479537392', mime_type: 'image/jpeg', sha256: 'abc' },
          },
        ],
      }),
    );

    expect(messages[0]).toMatchObject({
      kind: 'unsupported',
      text: '',
      mediaType: 'image',
      mediaId: '1479537392',
    });
  });

  it('records the quoted message when someone uses WhatsApp’s reply UI', () => {
    const { messages } = parseWebhook(
      envelope({
        metadata: METADATA,
        messages: [
          {
            from: '5215598765432',
            id: 'wamid.Q',
            timestamp: '7',
            type: 'text',
            text: { body: '¿y ese cuánto dura?' },
            context: { from: '5215512345678', id: 'wamid.PREV' },
          },
        ],
      }),
    );

    expect(messages[0]?.quotedMessageId).toBe('wamid.PREV');
  });

  it('skips a message with no id or no sender', () => {
    const { messages } = parseWebhook(
      envelope({
        metadata: METADATA,
        messages: [
          { id: 'wamid.NOFROM', timestamp: '8', type: 'text', text: { body: 'hola' } },
          { from: '5215598765432', timestamp: '9', type: 'text', text: { body: 'hola' } },
        ],
      }),
    );

    expect(messages).toEqual([]);
  });

  it('ignores a change for a field other than `messages`', () => {
    const { messages, statuses } = parseWebhook(
      envelope(
        { message_template_id: 1234, event: 'APPROVED' },
        'message_template_status_update',
      ),
    );

    expect(messages).toEqual([]);
    expect(statuses).toEqual([]);
  });

  it('returns nothing rather than throwing on a malformed payload', () => {
    for (const payload of [null, undefined, 42, 'nope', {}, { entry: 'not-an-array' }, { entry: [null] }]) {
      expect(() => parseWebhook(payload)).not.toThrow();
      expect(parseWebhook(payload)).toEqual({ messages: [], statuses: [] });
    }
  });

  it('recognises only the WhatsApp product', () => {
    expect(isWhatsAppWebhook({ object: 'whatsapp_business_account' })).toBe(true);
    expect(isWhatsAppWebhook({ object: 'instagram' })).toBe(false);
    expect(isWhatsAppWebhook(null)).toBe(false);
  });
});

describe('toReplyJob', () => {
  it('flattens a message into a job payload with the receive time', () => {
    const { messages } = parseWebhook(
      envelope({
        metadata: METADATA,
        contacts: [{ profile: { name: 'Ana' }, wa_id: '5215598765432' }],
        messages: [
          { from: '5215598765432', id: 'wamid.J', timestamp: '10', type: 'text', text: { body: 'hola' } },
        ],
      }),
    );

    const at = new Date('2026-03-01T12:00:00.000Z');
    expect(toReplyJob(messages[0]!, at)).toEqual({
      waMessageId: 'wamid.J',
      from: '5215598765432',
      phoneNumberId: '106540352242922',
      kind: 'text',
      text: 'hola',
      profileName: 'Ana',
      replyId: null,
      mediaType: null,
      receivedAt: '2026-03-01T12:00:00.000Z',
    });
  });
});
