import { describe, expect, it, vi } from 'vitest';
import { WhatsAppApiError, WhatsAppClient } from '../client.js';

/** A fetch that answers from a scripted list and records what it was sent. */
function fakeFetch(responses: Array<{ status: number; body: unknown }>) {
  const calls: Array<{ url: string; init: RequestInit; payload: Record<string, unknown> }> = [];
  let index = 0;

  const impl = (async (url: string | URL | Request, init?: RequestInit) => {
    const answer = responses[Math.min(index, responses.length - 1)]!;
    index += 1;
    calls.push({
      url: String(url),
      init: init ?? {},
      payload: JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>,
    });

    return {
      ok: answer.status >= 200 && answer.status < 300,
      status: answer.status,
      text: async () => (typeof answer.body === 'string' ? answer.body : JSON.stringify(answer.body)),
    } as Response;
  }) as unknown as typeof fetch;

  return { impl, calls };
}

const ACCEPTED = { status: 200, body: { messages: [{ id: 'wamid.SENT' }] } };

function client(fetchImpl: typeof fetch, maxRetries = 2) {
  return new WhatsAppClient({
    phoneNumberId: '106540352242922',
    accessToken: 'test-token',
    apiVersion: 'v21.0',
    maxRetries,
    fetchImpl,
  });
}

describe('sendText', () => {
  it('posts to the versioned messages endpoint with the bearer token', async () => {
    const { impl, calls } = fakeFetch([ACCEPTED]);
    const result = await client(impl).sendText('5215598765432', 'El curso dura 8 horas.');

    expect(result.messageIds).toEqual(['wamid.SENT']);
    expect(calls[0]!.url).toBe(
      'https://graph.facebook.com/v21.0/106540352242922/messages',
    );
    expect((calls[0]!.init.headers as Record<string, string>).Authorization).toBe('Bearer test-token');
    expect(calls[0]!.payload).toEqual({
      messaging_product: 'whatsapp',
      recipient_type: 'individual',
      to: '5215598765432',
      type: 'text',
      text: { body: 'El curso dura 8 horas.', preview_url: false },
    });
  });

  it('leaves link previews off, so an unverified price cannot arrive in a card', async () => {
    const { impl, calls } = fakeFetch([ACCEPTED]);
    await client(impl).sendText('52155', 'https://cruzroja.example/cursos');

    expect((calls[0]!.payload.text as { preview_url: boolean }).preview_url).toBe(false);
  });

  it('sends an over-long body as several ordered messages', async () => {
    const { impl, calls } = fakeFetch([ACCEPTED]);
    const result = await client(impl).sendText('52155', `${'a'.repeat(3000)}\n\n${'b'.repeat(3000)}`);

    expect(calls).toHaveLength(2);
    expect(result.messageIds).toHaveLength(2);
    expect((calls[0]!.payload.text as { body: string }).body.startsWith('a')).toBe(true);
    expect((calls[1]!.payload.text as { body: string }).body.startsWith('b')).toBe(true);
  });

  it('sends nothing at all for an empty body', async () => {
    const { impl, calls } = fakeFetch([ACCEPTED]);
    const result = await client(impl).sendText('52155', '   ');

    expect(calls).toHaveLength(0);
    expect(result.messageIds).toEqual([]);
  });
});

describe('failures', () => {
  it('does not retry a 400 — the same bad payload would fail identically', async () => {
    const { impl, calls } = fakeFetch([
      {
        status: 400,
        body: {
          error: {
            message: 'Invalid parameter',
            code: 100,
            error_subcode: 2494010,
            error_data: { details: 'Parameter to is not a valid phone number' },
          },
        },
      },
    ]);

    await expect(client(impl).sendText('nonsense', 'hola')).rejects.toBeInstanceOf(WhatsAppApiError);
    expect(calls).toHaveLength(1);
  });

  it('keeps Meta’s numeric code, which is the only thing that names the repair', async () => {
    const { impl } = fakeFetch([
      { status: 400, body: { error: { message: 'Re-engagement message', code: 131047 } } },
    ]);

    const error = await client(impl)
      .sendText('52155', 'hola')
      .catch((e: unknown) => e as WhatsAppApiError);

    expect(error).toBeInstanceOf(WhatsAppApiError);
    expect((error as WhatsAppApiError).isWindowExpired).toBe(true);
    expect((error as WhatsAppApiError).retryable).toBe(false);
  });

  it('flags an expired token so it is not mistaken for a transient failure', async () => {
    const { impl } = fakeFetch([
      { status: 401, body: { error: { message: 'Session expired', code: 190 } } },
    ]);

    const error = (await client(impl)
      .sendText('52155', 'hola')
      .catch((e: unknown) => e)) as WhatsAppApiError;

    expect(error.isAuthFailure).toBe(true);
    expect(error.retryable).toBe(false);
  });

  it('retries a 500 and succeeds on the second attempt', async () => {
    const { impl, calls } = fakeFetch([{ status: 500, body: { error: { message: 'oops' } } }, ACCEPTED]);
    const result = await client(impl).sendText('52155', 'hola');

    expect(calls).toHaveLength(2);
    expect(result.messageIds).toEqual(['wamid.SENT']);
  });

  it('gives up after the configured number of retries', async () => {
    const { impl, calls } = fakeFetch([{ status: 429, body: { error: { message: 'slow down' } } }]);

    await expect(client(impl).sendText('52155', 'hola')).rejects.toBeInstanceOf(WhatsAppApiError);
    expect(calls).toHaveLength(3); // the first attempt plus two retries
  });

  it('treats a transport failure as retryable', async () => {
    const failing = vi
      .fn()
      .mockRejectedValueOnce(new Error('ECONNRESET'))
      .mockResolvedValue({
        ok: true,
        status: 200,
        text: async () => JSON.stringify(ACCEPTED.body),
      } as Response) as unknown as typeof fetch;

    const result = await client(failing).sendText('52155', 'hola');
    expect(result.messageIds).toEqual(['wamid.SENT']);
  });

  it('reads an HTML error page without throwing a parse error over the status', async () => {
    const { impl } = fakeFetch([{ status: 502, body: '<html>Bad Gateway</html>' }]);
    const error = (await client(impl, 0)
      .sendText('52155', 'hola')
      .catch((e: unknown) => e)) as WhatsAppApiError;

    expect(error.status).toBe(502);
    expect(error.retryable).toBe(true);
  });
});

describe('other message shapes', () => {
  it('marks a message read', async () => {
    const { impl, calls } = fakeFetch([{ status: 200, body: { success: true } }]);
    await client(impl).markRead('wamid.IN');

    expect(calls[0]!.payload).toEqual({
      messaging_product: 'whatsapp',
      status: 'read',
      message_id: 'wamid.IN',
    });
  });

  it('sends a template with positional body parameters', async () => {
    const { impl, calls } = fakeFetch([ACCEPTED]);
    await client(impl).sendTemplate('52155', {
      name: 'seguimiento_curso',
      language: 'es_MX',
      variables: ['María', 'RCP y DEA'],
    });

    expect(calls[0]!.payload).toMatchObject({
      type: 'template',
      template: {
        name: 'seguimiento_curso',
        language: { code: 'es_MX' },
        components: [
          {
            type: 'body',
            parameters: [
              { type: 'text', text: 'María' },
              { type: 'text', text: 'RCP y DEA' },
            ],
          },
        ],
      },
    });
  });

  it('omits the components block when a template takes no variables', async () => {
    const { impl, calls } = fakeFetch([ACCEPTED]);
    await client(impl).sendTemplate('52155', { name: 'aviso', language: 'es_MX' });

    expect(calls[0]!.payload.template).not.toHaveProperty('components');
  });

  it('clips buttons to what Meta accepts rather than letting the send 400', async () => {
    const { impl, calls } = fakeFetch([ACCEPTED]);
    await client(impl).sendButtons('52155', 'Elige una opción', [
      { id: 'a', label: 'Inscribirme en este curso ahora mismo' },
      { id: 'b', label: 'Otros cursos' },
      { id: 'c', label: 'Menú' },
      { id: 'd', label: 'Sobra' },
    ]);

    const buttons = (
      calls[0]!.payload.interactive as { action: { buttons: Array<{ reply: { title: string } }> } }
    ).action.buttons;

    expect(buttons).toHaveLength(3);
    for (const button of buttons) expect(button.reply.title.length).toBeLessThanOrEqual(20);
  });
});
