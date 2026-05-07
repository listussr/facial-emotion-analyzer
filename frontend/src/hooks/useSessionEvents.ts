import { useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import type { Emotion } from '@/types';

export interface AnalyticsFace {
  bbox: [number, number, number, number]; // x1, y1, x2, y2 в пикселях кадра
  track_id: number;
  face_id: string;
  emotion: Emotion | null;
  emotion_scores: number[];
}

export interface AnalyticsPayload {
  camera_id: string;
  frame_id: string | number;
  faces: AnalyticsFace[];
}

interface State {
  latest: AnalyticsPayload | null;
  fps: number;
  connected: boolean;
}

/**
 * WebSocket-подписка на /api/events/{id}. Считает rolling FPS по приходящим
 * пакетам аналитики и держит последний снимок (faces + frame_id).
 */
export function useSessionEvents(sessionId: string | undefined) {
  const [state, setState] = useState<State>({ latest: null, fps: 0, connected: false });
  const fpsBuf = useRef<number[]>([]);

  useEffect(() => {
    if (!sessionId) return;
    const ws = new WebSocket(api.eventsWsUrl(sessionId));
    let alive = true;

    ws.onopen = () => alive && setState((s) => ({ ...s, connected: true }));
    ws.onclose = () => alive && setState((s) => ({ ...s, connected: false }));
    ws.onerror = () => alive && setState((s) => ({ ...s, connected: false }));

    ws.onmessage = (ev) => {
      if (!alive) return;
      try {
        const msg = JSON.parse(ev.data);
        if (msg.kind !== 'analytics') return;
        const data = msg.data as AnalyticsPayload;

        // rolling FPS: фиксируем timestamp каждого пакета, окно 30 сообщений
        const now = performance.now();
        fpsBuf.current.push(now);
        if (fpsBuf.current.length > 30) fpsBuf.current.shift();
        let fps = 0;
        if (fpsBuf.current.length >= 2) {
          const dt = (fpsBuf.current[fpsBuf.current.length - 1] - fpsBuf.current[0]) / 1000;
          fps = dt > 0 ? (fpsBuf.current.length - 1) / dt : 0;
        }
        setState({ latest: data, fps, connected: true });
      } catch {
        /* ignore */
      }
    };

    return () => {
      alive = false;
      ws.close();
    };
  }, [sessionId]);

  return state;
}
