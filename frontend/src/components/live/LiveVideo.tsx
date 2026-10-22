import { useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import { EMOTION_COLORS, EMOTION_RU, type Emotion } from '@/types';
import type { AnalyticsPayload } from '@/hooks/useSessionEvents';
import {
  ANALYTICS_IDLE_MS,
  BufferedFrame,
  KEEP_MS,
  LagEstimator,
  OverlayFace,
  displayDelay,
  dropOlder,
  facesAt,
  insertByPts,
  lastAtOrBefore,
} from './sync';

interface Props {
  sessionId: string;
  /** Без подписей у рамок (маленькие тайлы) */
  hideLabels?: boolean;
  /** Показывать FPS показа и задержку */
  showStats?: boolean;
  className?: string;
}

interface Stats {
  fps: number;
  delayMs: number;
}

const UNDETERMINED_COLOR = '#cbd5e1';

/**
 * Видео сессии с рамками, нарисованными в браузере.
 *
 * Кадры — /api/live/{id} (JPEG + pts_ms), результаты анализа — /api/events/{id}.
 * Видео идёт с частотой источника, а рамки берутся из аналитики, сопоставленной
 * по pts (см. sync.ts). Серверный аннотатор для показа не нужен.
 */
export default function LiveVideo({ sessionId, hideLabels = false, showStats = false, className = '' }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [stats, setStats] = useState<Stats>({ fps: 0, delayMs: 0 });

  useEffect(() => {
    const frames: BufferedFrame[] = [];
    const analytics: AnalyticsPayload[] = [];
    const lag = new LagEstimator();
    let lastAnalyticsAt = 0;
    let alive = true;
    let raf = 0;
    let lastDrawnPts = -1;
    let drawn: number[] = [];
    let lastStatsAt = 0;

    const newestPts = () => (frames.length ? frames[frames.length - 1].pts : null);

    // --- кадры ---
    const videoWs = new WebSocket(api.liveWsUrl(sessionId));
    videoWs.binaryType = 'arraybuffer';
    videoWs.onmessage = (ev) => {
      if (!alive || typeof ev.data === 'string') return; // ping
      const buf = ev.data as ArrayBuffer;
      const view = new DataView(buf);
      const headerLen = view.getUint32(0);
      let header: { pts_ms: number | null };
      try {
        header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, headerLen)));
      } catch {
        return;
      }
      if (header.pts_ms == null) return;
      const pts = header.pts_ms;
      const blob = new Blob([new Uint8Array(buf, 4 + headerLen)], { type: 'image/jpeg' });
      createImageBitmap(blob)
        .then((bitmap) => {
          if (!alive) {
            bitmap.close();
            return;
          }
          // Новый поток с начала (повтор файла, перезапуск) — старые кадры не нужны
          const newest = newestPts();
          if (newest !== null && pts < newest - KEEP_MS) {
            frames.splice(0).forEach((f) => f.bitmap.close());
            analytics.splice(0);
            lag.reset();
            lastDrawnPts = -1;
          }
          insertByPts(frames, { pts, bitmap });
          dropOlder(frames, pts - KEEP_MS, (f) => f.bitmap.close());
        })
        .catch(() => undefined);
    };

    // --- аналитика ---
    const eventsWs = new WebSocket(api.eventsWsUrl(sessionId));
    eventsWs.onmessage = (ev) => {
      if (!alive) return;
      let msg: { kind: string; data: AnalyticsPayload };
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.kind !== 'analytics' || msg.data.pts_ms == null) return;
      const a = msg.data;
      const item = { ...a, pts: a.pts_ms as number };
      insertByPts(analytics as (AnalyticsPayload & { pts: number })[], item);
      dropOlder(analytics as (AnalyticsPayload & { pts: number })[], item.pts - KEEP_MS);
      const newest = newestPts();
      if (newest !== null) lag.add(newest, item.pts);
      lastAnalyticsAt = performance.now();
    };

    // --- отрисовка ---
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      const canvas = canvasRef.current;
      const newest = newestPts();
      if (!canvas || newest === null) return;

      const active = now - lastAnalyticsAt < ANALYTICS_IDLE_MS;
      const delay = displayDelay(lag, active);
      const target = newest - delay;
      let idx = lastAtOrBefore(frames, target, (f) => f.pts);
      if (idx < 0) idx = 0;
      const frame = frames[idx];
      // Кадры раньше показанного больше не понадобятся
      if (idx > 0) frames.splice(0, idx).forEach((f) => f.bitmap.close());

      if (frame.pts !== lastDrawnPts) {
        lastDrawnPts = frame.pts;
        const { width, height } = frame.bitmap;
        if (canvas.width !== width || canvas.height !== height) {
          canvas.width = width;
          canvas.height = height;
        }
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.drawImage(frame.bitmap, 0, 0);
        if (active) drawFaces(ctx, facesAt(analytics, frame.pts), width, hideLabels);
        drawn.push(now);
      }

      if (showStats && now - lastStatsAt > 1000) {
        drawn = drawn.filter((t) => now - t < 1000);
        setStats({ fps: drawn.length, delayMs: Math.round(delay) });
        lastStatsAt = now;
      }
    };
    raf = requestAnimationFrame(draw);

    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      videoWs.close();
      eventsWs.close();
      frames.splice(0).forEach((f) => f.bitmap.close());
    };
  }, [sessionId, hideLabels, showStats]);

  return (
    <>
      <canvas ref={canvasRef} className={className} />
      {showStats && (
        <div className="absolute top-9 left-2 text-[10px] font-mono text-white/80 z-10 pointer-events-none">
          {stats.fps} FPS · задержка {stats.delayMs} мс
        </div>
      )}
    </>
  );
}

function drawFaces(ctx: CanvasRenderingContext2D, faces: OverlayFace[], width: number, hideLabels: boolean) {
  const line = Math.max(2, Math.round(width / 400));
  const fontPx = Math.max(12, Math.round(width / 60));
  ctx.font = `600 ${fontPx}px ui-monospace, monospace`;
  ctx.textBaseline = 'bottom';
  for (const f of faces) {
    const [x1, y1, x2, y2] = f.box;
    const emo = f.emotion as Emotion | null;
    const scored = f.emotion_scores && f.emotion_scores.length > 0;
    const color = emo ? EMOTION_COLORS[emo] ?? UNDETERMINED_COLOR : UNDETERMINED_COLOR;

    ctx.lineWidth = line;
    ctx.strokeStyle = color;
    ctx.setLineDash(emo ? [] : [line * 3, line * 2]);
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    ctx.setLineDash([]);

    if (hideLabels) continue;
    const label = emo
      ? `${EMOTION_RU[emo] ?? emo} ${Math.max(...f.emotion_scores).toFixed(2)}`
      : scored
      ? 'не определено'
      : '…';
    const pad = Math.round(fontPx / 4);
    const tw = ctx.measureText(label).width + pad * 2;
    const ty = Math.max(fontPx + pad * 2, y1);
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.9;
    ctx.fillRect(x1, ty - fontPx - pad * 2, tw, fontPx + pad * 2);
    ctx.globalAlpha = 1;
    ctx.fillStyle = '#0f172a';
    ctx.fillText(label, x1 + pad, ty - pad);
  }
}
