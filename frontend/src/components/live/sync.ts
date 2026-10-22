/**
 * Синхронизация исходных кадров и результатов аналитики по pts_ms.
 *
 * Кадры приходят сразу, результаты анализа — с задержкой (детекция, модели,
 * слияние). Поэтому видео показывается с задержкой ≈ отставанию аналитики,
 * а рамки для показанного кадра интерполируются между двумя ближайшими
 * результатами по времени: так они совпадают с лицом даже при детекции не на
 * каждом кадре и при анализе медленнее источника.
 */
import type { AnalyticsFace, AnalyticsPayload } from '@/hooks/useSessionEvents';

export interface BufferedFrame {
  pts: number;
  bitmap: ImageBitmap;
}

/** Сколько истории держать (мс по pts) */
export const KEEP_MS = 4000;
/** Результат старше кадра больше чем на столько — рамки не рисуем (лицо могло уйти) */
export const MAX_ANALYTICS_GAP_MS = 600;
/** Запас к измеренному отставанию аналитики */
export const DELAY_MARGIN_MS = 80;
export const MAX_DELAY_MS = 2000;
/** Аналитики нет дольше — показываем видео без задержки и без рамок */
export const ANALYTICS_IDLE_MS = 2000;

/** Вставка по возрастанию pts (кадры декодируются асинхронно и могут прийти не по порядку). */
export function insertByPts<T extends { pts: number }>(arr: T[], item: T): void {
  let i = arr.length;
  while (i > 0 && arr[i - 1].pts > item.pts) i--;
  arr.splice(i, 0, item);
}

export class LagEstimator {
  private ema: number | null = null;

  constructor(private alpha = 0.1) {}

  /** Отставание аналитики: pts самого свежего кадра − pts пришедшего результата. */
  add(newestFramePts: number, analyticsPts: number): void {
    const lag = Math.max(0, newestFramePts - analyticsPts);
    this.ema = this.ema === null ? lag : this.ema + this.alpha * (lag - this.ema);
  }

  get value(): number {
    return this.ema ?? 0;
  }

  reset(): void {
    this.ema = null;
  }
}

/** Задержка показа: отставание аналитики + запас; без аналитики — 0. */
export function displayDelay(lag: LagEstimator, analyticsActive: boolean): number {
  if (!analyticsActive) return 0;
  return Math.min(MAX_DELAY_MS, Math.max(0, lag.value + DELAY_MARGIN_MS));
}

/** Индекс последнего элемента с pts ≤ target (или -1). */
export function lastAtOrBefore<T>(arr: T[], target: number, pts: (x: T) => number): number {
  let lo = 0;
  let hi = arr.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (pts(arr[mid]) <= target) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}

export interface OverlayFace extends AnalyticsFace {
  /** Рамка, интерполированная на pts показанного кадра */
  box: [number, number, number, number];
}

/**
 * Лица для кадра с данным pts: рамки линейно интерполируются между результатом
 * до кадра (a0) и после него (a1) по track_id; подписи — из a0.
 */
export function facesAt(analytics: AnalyticsPayload[], framePts: number): OverlayFace[] {
  const i0 = lastAtOrBefore(analytics, framePts, (a) => a.pts_ms ?? -Infinity);
  if (i0 < 0) return [];
  const a0 = analytics[i0];
  const p0 = a0.pts_ms as number;
  if (framePts - p0 > MAX_ANALYTICS_GAP_MS) return [];
  const a1 = analytics[i0 + 1];
  const p1 = a1?.pts_ms;
  const t = a1 && p1 != null && p1 > p0 ? (framePts - p0) / (p1 - p0) : 0;
  const next = new Map<number, AnalyticsFace>();
  if (t > 0) for (const f of a1.faces) next.set(f.track_id, f);
  return a0.faces.map((f) => {
    const g = next.get(f.track_id);
    const box = g
      ? (f.bbox.map((v, k) => v + (g.bbox[k] - v) * t) as [number, number, number, number])
      : f.bbox;
    return { ...f, box };
  });
}

/** Выбросить из начала буфера всё старше границы (для кадров — с освобождением памяти). */
export function dropOlder<T extends { pts: number }>(arr: T[], minPts: number, onDrop?: (x: T) => void): void {
  let n = 0;
  while (n < arr.length && arr[n].pts < minPts) {
    onDrop?.(arr[n]);
    n++;
  }
  if (n) arr.splice(0, n);
}
