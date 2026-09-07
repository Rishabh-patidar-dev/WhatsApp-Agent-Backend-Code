import { describe, expect, it } from 'vitest';
import {
  WHATSAPP_LIMITS,
  clip,
  clipButtons,
  clipSections,
  splitForSend,
} from '../format.js';

describe('clip', () => {
  it('collapses whitespace so a newline cannot smuggle length past a label', () => {
    expect(clip('  Curso   de\nprimeros  auxilios ', 40)).toBe('Curso de primeros auxilios');
  });

  it('never exceeds the limit, and marks that it cut', () => {
    const out = clip('Reanimación cardiopulmonar y desfibrilación externa', WHATSAPP_LIMITS.ROW_TITLE);
    expect(out.length).toBeLessThanOrEqual(WHATSAPP_LIMITS.ROW_TITLE);
    expect(out.endsWith('…')).toBe(true);
  });

  it('handles an empty string and a zero limit without throwing', () => {
    expect(clip('', 10)).toBe('');
    expect(clip('hola', 0)).toBe('');
  });
});

describe('splitForSend', () => {
  it('leaves anything within the limit alone', () => {
    expect(splitForSend('El curso dura 8 horas.')).toEqual(['El curso dura 8 horas.']);
  });

  it('drops an empty body rather than sending a blank message', () => {
    expect(splitForSend('   ')).toEqual([]);
  });

  it('splits on the paragraph boundary and keeps every part sendable', () => {
    const paragraph = `${'a'.repeat(3000)}\n\n${'b'.repeat(3000)}`;
    const parts = splitForSend(paragraph);

    expect(parts).toHaveLength(2);
    expect(parts[0]).toBe('a'.repeat(3000));
    expect(parts[1]).toBe('b'.repeat(3000));
    for (const part of parts) expect(part.length).toBeLessThanOrEqual(WHATSAPP_LIMITS.TEXT_BODY);
  });

  it('hard-cuts a single unbroken run — a long URL has no boundary to find', () => {
    const parts = splitForSend('x'.repeat(9000));

    expect(parts).toHaveLength(3);
    for (const part of parts) expect(part.length).toBeLessThanOrEqual(WHATSAPP_LIMITS.TEXT_BODY);
    expect(parts.join('')).toHaveLength(9000);
  });

  it('loses no words when splitting on spaces', () => {
    const words = Array.from({ length: 1500 }, (_, i) => `palabra${i}`);
    const parts = splitForSend(words.join(' '));

    expect(parts.length).toBeGreaterThan(1);
    expect(parts.join(' ').split(' ')).toEqual(words);
  });
});

describe('clipButtons', () => {
  it('keeps three at most and fits every label', () => {
    const out = clipButtons([
      { id: 'a', label: 'Inscribirme en este curso ahora' },
      { id: 'b', label: 'Otros cursos' },
      { id: 'c', label: 'Menú' },
      { id: 'd', label: 'Nunca enviado' },
    ]);

    expect(out).toHaveLength(WHATSAPP_LIMITS.BUTTONS_PER_MESSAGE);
    expect(out.map((b) => b.id)).toEqual(['a', 'b', 'c']);
    for (const button of out) {
      expect(button.label.length).toBeLessThanOrEqual(WHATSAPP_LIMITS.BUTTON_LABEL);
    }
  });
});

describe('clipSections', () => {
  it('spends the ten-row budget across sections, not per section', () => {
    const section = (title: string, rows: number) => ({
      title,
      rows: Array.from({ length: rows }, (_, i) => ({ id: `${title}:${i}`, title: `fila ${i}` })),
    });

    const out = clipSections([section('uno', 6), section('dos', 6), section('tres', 6)]);
    const total = out.reduce((sum, s) => sum + s.rows.length, 0);

    expect(total).toBe(WHATSAPP_LIMITS.LIST_ROWS_TOTAL);
    expect(out.map((s) => s.rows.length)).toEqual([6, 4]);
  });

  it('fits row titles and descriptions, and omits an absent description', () => {
    const [section] = clipSections([
      {
        title: 'Cursos para profesionales de la salud',
        rows: [
          {
            id: 'crs:HP038',
            title: 'Soporte vital básico y avanzado para adultos',
            description: 'Un texto largo '.repeat(20),
          },
          { id: 'crs:HP039', title: 'Corto' },
        ],
      },
    ]);

    expect(section!.title.length).toBeLessThanOrEqual(WHATSAPP_LIMITS.SECTION_TITLE);
    expect(section!.rows[0]!.title.length).toBeLessThanOrEqual(WHATSAPP_LIMITS.ROW_TITLE);
    expect(section!.rows[0]!.description!.length).toBeLessThanOrEqual(WHATSAPP_LIMITS.ROW_DESCRIPTION);
    expect(section!.rows[1]).not.toHaveProperty('description');
  });
});
