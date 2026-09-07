/**
 * CHECKPOINT 10 — the WhatsApp channel is wired end to end.
 *
 * Everything below can be got wrong in a way that type checking and unit tests
 * cannot see, because the failures live in the seam between Meta, express and
 * pg-boss:
 *
 *   1. the GET handshake echoes `hub.challenge` as plain text
 *   2. a correctly signed POST is accepted — proving the raw body survived
 *   3. a *tampered* POST is rejected — proving the check is not a no-op
 *   4. the message became a job and the worker consumed it
 *   5. a redelivery of the same message id does not produce a second answer
 *
 * Point 3 is the one worth having in a script. A signature check that always
 * passes looks exactly like a signature check that works, right up until
 * somebody posts a fabricated customer message at the public URL.
 *
 * Nothing here touches Meta. The payload is synthetic and signed with your own
 * app secret, which is precisely what Meta does — so this exercises the real
 * middleware, the real route and the real queue without a phone.
 *
 * Run it with both processes up:
 *   npm run dev:api        (one terminal)
 *   npm run dev:worker     (another)
 *   npm run checkpoint:10  (a third)
 *
 * Add `--send=<E.164 digits>` to also send one real WhatsApp message, e.g.
 *   npm run checkpoint:10 -- --send=5215598765432
 * That number must have messaged your test number within the last 24 hours,
 * or Meta refuses the send with error 131047 — which is the window doing its
 * job, not a broken setup.
 */
import { createHmac, randomUUID } from 'node:crypto';
import { WhatsAppClient } from '@rag/channels';
import { createDbClient } from '@rag/db';
import { getApiConfig, getDatabaseConfig, getWhatsAppConfig, isWhatsAppConfigured } from '@rag/shared';

let passed = true;

function assertTrue(label: string, ok: boolean, detail = ''): void {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ` — ${detail}` : ''}`);
  if (!ok) passed = false;
}

function heading(text: string): void {
  console.log(`\n${'═'.repeat(72)}\n  ${text}\n${'═'.repeat(72)}`);
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

function argValue(name: string): string | null {
  const hit = process.argv.slice(2).find((arg) => arg.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
}

/** The exact envelope Meta sends for one inbound text message. */
function inboundPayload(waMessageId: string, from: string, phoneNumberId: string, text: string): string {
  return JSON.stringify({
    object: 'whatsapp_business_account',
    entry: [
      {
        id: '0',
        changes: [
          {
            field: 'messages',
            value: {
              messaging_product: 'whatsapp',
              metadata: { display_phone_number: '0', phone_number_id: phoneNumberId },
              contacts: [{ profile: { name: 'Checkpoint 10' }, wa_id: from }],
              messages: [
                {
                  from,
                  id: waMessageId,
                  timestamp: String(Math.floor(Date.now() / 1000)),
                  type: 'text',
                  text: { body: text },
                },
              ],
            },
          },
        ],
      },
    ],
  });
}

function sign(body: string, appSecret: string): string {
  return `sha256=${createHmac('sha256', appSecret).update(body, 'utf8').digest('hex')}`;
}

async function main(): Promise<void> {
  heading('CHECKPOINT 10 — WhatsApp channel');

  if (!isWhatsAppConfigured()) {
    console.error(
      '\n  WHATSAPP_* is not configured. Copy the four keys from .env.example\n' +
        '  and fill them in from the Meta App dashboard.\n',
    );
    process.exit(1);
  }

  const whatsapp = getWhatsAppConfig();
  const api = getApiConfig();
  const base = api.API_PUBLIC_ORIGIN;
  const db = createDbClient({ connectionString: getDatabaseConfig().DATABASE_URL });

  console.log(`\n  API            ${base}`);
  console.log(`  Phone number   ${whatsapp.WHATSAPP_PHONE_NUMBER_ID}`);
  console.log(`  Graph version  ${whatsapp.WHATSAPP_API_VERSION}`);

  // ── 1. the GET handshake ─────────────────────────────────────────────
  heading('1. Subscription handshake (GET)');

  const challenge = String(Math.floor(Math.random() * 1e9));
  const verifyUrl =
    `${base}/webhooks/whatsapp?hub.mode=subscribe` +
    `&hub.verify_token=${encodeURIComponent(whatsapp.WHATSAPP_VERIFY_TOKEN)}` +
    `&hub.challenge=${challenge}`;

  const verified = await fetch(verifyUrl);
  const echoed = (await verified.text()).trim();
  assertTrue('challenge echoed verbatim as text', verified.status === 200 && echoed === challenge,
    `status ${verified.status}, body ${JSON.stringify(echoed.slice(0, 40))}`);

  const wrongToken = await fetch(
    `${base}/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=${challenge}`,
  );
  assertTrue('a wrong verify token is refused', wrongToken.status === 403, `status ${wrongToken.status}`);

  // ── 2 & 3. signature verification ────────────────────────────────────
  heading('2. Signature verification (POST)');

  const from = argValue('from') ?? '5215500000000';
  const waMessageId = `wamid.CHECKPOINT10.${randomUUID()}`;
  const body = inboundPayload(waMessageId, from, whatsapp.WHATSAPP_PHONE_NUMBER_ID, '¿Qué cursos ofrecen?');

  const tampered = await fetch(`${base}/webhooks/whatsapp`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Hub-Signature-256': sign(`${body} `, whatsapp.WHATSAPP_APP_SECRET),
    },
    body,
  });
  assertTrue('a payload whose signature does not match is rejected', tampered.status === 403,
    `status ${tampered.status}`);

  const unsigned = await fetch(`${base}/webhooks/whatsapp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  });
  assertTrue('an unsigned payload is rejected', unsigned.status === 403, `status ${unsigned.status}`);

  const accepted = await fetch(`${base}/webhooks/whatsapp`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Hub-Signature-256': sign(body, whatsapp.WHATSAPP_APP_SECRET),
    },
    body,
  });
  const acceptedBody = (await accepted.json()) as { status?: string; enqueued?: number };
  assertTrue('a correctly signed payload is accepted and enqueued',
    accepted.status === 200 && acceptedBody.enqueued === 1,
    `status ${accepted.status}, body ${JSON.stringify(acceptedBody)}`);

  // ── 4. the worker answered it ────────────────────────────────────────
  heading('3. The worker generated and stored an answer');

  let userMessageId: string | null = null;
  const deadline = Date.now() + 60_000;

  while (Date.now() < deadline) {
    const row = await db.message.findUnique({ where: { waMessageId }, select: { id: true } });
    if (row) {
      userMessageId = row.id;
      break;
    }
    process.stdout.write('    waiting for the worker…\r');
    await sleep(2000);
  }

  assertTrue('the inbound message was stored with its WhatsApp id', userMessageId !== null,
    userMessageId ? '' : 'nothing after 60s — is `npm run dev:worker` running?');

  if (userMessageId) {
    const user = await db.message.findUnique({
      where: { id: userMessageId },
      select: { conversationId: true, createdAt: true },
    });

    const reply = await db.message.findFirst({
      where: {
        conversationId: user!.conversationId,
        role: 'ASSISTANT',
        createdAt: { gte: user!.createdAt },
      },
      orderBy: { createdAt: 'asc' },
      select: { content: true, status: true, topScore: true, modelId: true, latencyMs: true },
    });

    assertTrue('an assistant reply was written', reply !== null);

    if (reply) {
      console.log(`\n    model     ${reply.modelId ?? '—'}  (${reply.latencyMs ?? '—'} ms)`);
      console.log(`    topScore  ${reply.topScore ?? '—'}`);
      console.log(`    status    ${reply.status}`);
      console.log(`\n${reply.content.split('\n').map((line) => `    │ ${line}`).join('\n')}\n`);

      // FAILED is expected against a fabricated number: Meta has no such
      // conversation, so the send is refused. The pipeline still ran.
      assertTrue('the reply is not empty', reply.content.trim().length > 0);
    }
  }

  // ── 5. idempotency ───────────────────────────────────────────────────
  heading('4. A redelivery does not produce a second answer');

  const before = await db.message.count();

  const redelivered = await fetch(`${base}/webhooks/whatsapp`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Hub-Signature-256': sign(body, whatsapp.WHATSAPP_APP_SECRET),
    },
    body,
  });
  assertTrue('the redelivery is acknowledged', redelivered.status === 200);

  await sleep(8000);
  const after = await db.message.count();
  assertTrue('no new messages were written', after === before, `${before} → ${after}`);

  // ── 6. optional: a real send ─────────────────────────────────────────
  const sendTo = argValue('send');
  if (sendTo) {
    heading('5. A real WhatsApp message');

    const client = new WhatsAppClient({
      phoneNumberId: whatsapp.WHATSAPP_PHONE_NUMBER_ID,
      accessToken: whatsapp.WHATSAPP_ACCESS_TOKEN,
      apiVersion: whatsapp.WHATSAPP_API_VERSION,
      timeoutMs: whatsapp.WHATSAPP_REQUEST_TIMEOUT_SECONDS * 1000,
    });

    try {
      const sent = await client.sendText(sendTo, 'Checkpoint 10: la conexión con WhatsApp funciona.');
      assertTrue('Meta accepted the message', sent.messageIds.length > 0, sent.messageIds.join(', '));
    } catch (error) {
      assertTrue('Meta accepted the message', false, error instanceof Error ? error.message : String(error));
    }
  } else {
    console.log('\n  (skipping the real send — pass --send=<number> to include it)');
  }

  await db.$disconnect();

  heading(passed ? 'CHECKPOINT 10 PASSED' : 'CHECKPOINT 10 FAILED');
  process.exit(passed ? 0 : 1);
}

main().catch((error: unknown) => {
  console.error('\ncheckpoint 10 crashed:', error);
  process.exit(1);
});
