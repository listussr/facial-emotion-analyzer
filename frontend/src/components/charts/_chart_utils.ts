import { EMOTION_RU, EMOTION_COLORS, Emotion, TrackHistory } from '@/types';

/** Канонический порядок эмоций — должен совпадать с порядком в `scores`. */
export const EMOTION_ORDER: Emotion[] = [
  'anger', 'contempt', 'disgust', 'fear',
  'happy', 'neutral', 'sad', 'surprise',
];

export interface FlatSample {
  t: number;
  scores: number[]; // длина 8, в порядке EMOTION_ORDER
  label?: string | null;
}

/**
 * Сплющивает таймсерии (треки) в один временной ряд, отфильтровывая по
 * выделенному окну. Дополнительно опускает сэмплы без полного 8-элементного
 * вектора — таких не должно быть, но защита не помешает.
 *
 * Если сэмплов слишком много (> maxPoints), берём равномерно прореженную выборку,
 * чтобы SVG не раздувался до тысяч элементов и анимация оставалась плавной.
 */
export function flattenSamples(
  tracks: TrackHistory[],
  range: [number, number] | null,
  maxPoints = 600
): FlatSample[] {
  const all: FlatSample[] = [];
  for (const t of tracks) {
    for (const s of t.samples || []) {
      if (!Array.isArray(s.scores) || s.scores.length < EMOTION_ORDER.length) continue;
      if (range) {
        if (s.t < range[0] || s.t > range[1]) continue;
      }
      all.push({ t: s.t, scores: s.scores, label: s.label });
    }
  }
  all.sort((a, b) => a.t - b.t);

  if (all.length <= maxPoints) return all;
  // Равномерное прореживание — каждый k-й сэмпл.
  const step = all.length / maxPoints;
  const out: FlatSample[] = [];
  for (let i = 0; i < maxPoints; i++) {
    out.push(all[Math.min(all.length - 1, Math.floor(i * step))]);
  }
  return out;
}

export function tsRange(samples: FlatSample[]): { tMin: number; tMax: number; span: number } {
  if (samples.length === 0) return { tMin: 0, tMax: 1, span: 1 };
  const tMin = samples[0].t;
  const tMax = samples[samples.length - 1].t;
  return { tMin, tMax, span: Math.max(0.001, tMax - tMin) };
}

export function formatClock(t: number): string {
  if (!t) return '—';
  return new Date(t * 1000).toLocaleTimeString('ru-RU');
}

export { EMOTION_RU, EMOTION_COLORS };
