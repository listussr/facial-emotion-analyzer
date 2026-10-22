import {
  EMOTION_RU, EMOTION_COLORS, Emotion, TrackHistory, CURRENT_EMOTIONS, LEGACY_LABELS,
} from '@/types';

/** Порядок осей по умолчанию — текущие 6 классов CREMA-D. */
export const EMOTION_ORDER: Emotion[] = [...CURRENT_EMOTIONS];

/** Классы, к которым относятся scores трека (у старых записей без labels — 8 классов). */
export function trackLabels(t: TrackHistory): Emotion[] {
  return t.labels && t.labels.length > 0 ? t.labels : LEGACY_LABELS;
}

/**
 * Оси графиков для набора треков: текущие классы, затем классы старых
 * записей, если они встречаются (у новых данных — ровно 6 осей).
 */
export function chartOrder(tracks: TrackHistory[]): Emotion[] {
  const order: Emotion[] = [...EMOTION_ORDER];
  for (const t of tracks) {
    for (const name of trackLabels(t)) {
      if (!order.includes(name)) order.push(name);
    }
  }
  return order;
}

export interface FlatSample {
  t: number;
  scores: number[]; // в порядке переданного order (по умолчанию EMOTION_ORDER)
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
  maxPoints = 600,
  order: Emotion[] = EMOTION_ORDER
): FlatSample[] {
  const all: FlatSample[] = [];
  for (const t of tracks) {
    // scores записи раскладываются по именам классов: у старых 8-классовых
    // и новых 6-классовых записей разный порядок
    const labels = trackLabels(t);
    const index = order.map((name) => labels.indexOf(name));
    for (const s of t.samples || []) {
      if (!Array.isArray(s.scores) || s.scores.length < labels.length) continue;
      if (range) {
        if (s.t < range[0] || s.t > range[1]) continue;
      }
      all.push({ t: s.t, scores: index.map((i) => (i >= 0 ? s.scores[i] ?? 0 : 0)), label: s.label });
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
