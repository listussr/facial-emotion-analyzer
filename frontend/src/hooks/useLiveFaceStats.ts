import { useEffect, useRef, useState } from 'react';
import type { AnalyticsPayload } from './useSessionEvents';
import type { Emotion } from '@/types';

export interface FaceStats {
  /** Стабильный ключ — track_id ByteTrack/DeepSORT, уникален в рамках сессии. */
  key: number;
  /** Реальный face_id из БД (UUID) или 'pending-...' пока идёт идентификация. */
  face_id: string;
  track_id: number;
  label_counts: Record<string, number>;
  samples: { t: number; label: Emotion; scores: number[] }[];
  current_label: Emotion | null;
  current_scores: number[];
  last_seen: number;
}

/**
 * Накапливает per-face статистику из потока WS-событий и отдаёт snapshot,
 * обновляющийся раз в `flushIntervalMs` (по умолчанию 1с).
 *
 * Ключ — track_id (а не face_id), потому что сразу после появления трека
 * face_id ещё `pending-init`, и десятки треков, у которых идентификация
 * не завершилась, сливались бы в одну запись. К моменту, когда реальный
 * UUID прилетит — мы просто обновим поле `face_id` у уже существующей
 * записи, не теряя накопленные сэмплы.
 */
export function useLiveFaceStats(latest: AnalyticsPayload | null, flushIntervalMs = 1000) {
  const accRef = useRef<Map<number, FaceStats>>(new Map());
  const [snapshot, setSnapshot] = useState<FaceStats[]>([]);

  useEffect(() => {
    if (!latest) return;
    const t = Date.now() / 1000;
    const acc = accRef.current;
    for (const f of latest.faces) {
      const key = f.track_id;
      let entry = acc.get(key);
      if (!entry) {
        entry = {
          key,
          face_id: f.face_id,
          track_id: f.track_id,
          label_counts: {},
          samples: [],
          current_label: null,
          current_scores: [],
          last_seen: t,
        };
        acc.set(key, entry);
      }
      // face_id может быть pending → uuid; обновляем по факту
      entry.face_id = f.face_id;
      entry.last_seen = t;
      if (f.emotion) {
        entry.current_label = f.emotion as Emotion;
        entry.current_scores = f.emotion_scores || entry.current_scores;
        entry.label_counts[f.emotion] = (entry.label_counts[f.emotion] || 0) + 1;
        entry.samples.push({
          t,
          label: f.emotion as Emotion,
          scores: f.emotion_scores || [],
        });
        if (entry.samples.length > 600) {
          entry.samples.splice(0, entry.samples.length - 600);
        }
      }
    }
  }, [latest]);

  useEffect(() => {
    const tick = () => {
      const list = Array.from(accRef.current.values()).sort(
        (a, b) => b.last_seen - a.last_seen
      );
      setSnapshot(list);
    };
    tick();
    const id = window.setInterval(tick, flushIntervalMs);
    return () => window.clearInterval(id);
  }, [flushIntervalMs]);

  return snapshot;
}
