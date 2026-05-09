import { useMemo, useRef, useState } from 'react';
import { EMOTION_COLORS, EMOTION_RU, Emotion, TrackHistory } from '@/types';

interface Props {
  /** Один трек или список треков (рисуются на одном таймлайне). */
  tracks: TrackHistory[];
  height?: number;
  /** Отображаемый диапазон [tStart, tEnd] в секундах (Unix). Если задан —
   *  таймлайн всё равно покрывает весь диапазон, а внутри подсвечивает
   *  выделение. Используется для «навигатора» при зуме. */
  selectedRange?: [number, number] | null;
  /** Если задан — таймлайн становится интерактивным: клик-перетаскиванием
   *  пользователь выбирает диапазон, по mouseup вызывается этот колбэк. */
  onRangeSelect?: (range: [number, number] | null) => void;
  /** Скрыть легенду эмоций под таймлайном. */
  hideLegend?: boolean;
}

const W = 600;

export default function EmotionTimeline({
  tracks,
  height = 80,
  selectedRange = null,
  onRangeSelect,
  hideLegend = false,
}: Props) {
  const samples = useMemo(
    () =>
      tracks
        .flatMap((t) =>
          (t.samples || []).map((s) => ({
            t: s.t,
            label: s.label as Emotion,
          }))
        )
        .sort((a, b) => (a.t || 0) - (b.t || 0)),
    [tracks]
  );

  const tMin = samples.length ? samples[0].t : 0;
  const tMax = samples.length ? samples[samples.length - 1].t : 1;
  const span = Math.max(0.001, tMax - tMin);
  const H = height;

  const svgRef = useRef<SVGSVGElement | null>(null);
  const [dragStart, setDragStart] = useState<number | null>(null);
  const [dragEnd, setDragEnd] = useState<number | null>(null);
  const interactive = !!onRangeSelect;

  if (samples.length === 0) {
    return <p className="text-sm text-slate-500">Сэмплов пока нет.</p>;
  }

  const dt = span / Math.max(1, samples.length - 1);
  const pxPerSec = W / span;
  const rectW = Math.max(2, dt * pxPerSec);

  function pxToTs(clientX: number): number {
    const svg = svgRef.current;
    if (!svg) return tMin;
    const rect = svg.getBoundingClientRect();
    const x = ((clientX - rect.left) / rect.width) * W;
    const ts = tMin + (Math.max(0, Math.min(W, x)) / W) * span;
    return ts;
  }

  function onMouseDown(e: React.MouseEvent) {
    if (!interactive) return;
    const ts = pxToTs(e.clientX);
    setDragStart(ts);
    setDragEnd(ts);
  }
  function onMouseMove(e: React.MouseEvent) {
    if (!interactive || dragStart === null) return;
    setDragEnd(pxToTs(e.clientX));
  }
  function onMouseUp() {
    if (!interactive) return;
    if (dragStart !== null && dragEnd !== null) {
      const a = Math.min(dragStart, dragEnd);
      const b = Math.max(dragStart, dragEnd);
      // Слишком короткое выделение трактуем как клик → сброс
      if (b - a < span * 0.005) {
        onRangeSelect?.(null);
      } else {
        onRangeSelect?.([a, b]);
      }
    }
    setDragStart(null);
    setDragEnd(null);
  }

  const sel =
    dragStart !== null && dragEnd !== null
      ? ([Math.min(dragStart, dragEnd), Math.max(dragStart, dragEnd)] as [number, number])
      : selectedRange;

  return (
    <div className="w-full">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        width="100%"
        height={H}
        style={{ display: 'block', cursor: interactive ? 'crosshair' : 'default' }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={() => {
          if (dragStart !== null) onMouseUp();
        }}
      >
        <rect x={0} y={0} width={W} height={H} fill="rgba(99,102,241,0.05)" rx={6} />
        {samples.map((s, i) => {
          const x = ((s.t - tMin) / span) * W;
          const color = EMOTION_COLORS[s.label] || '#94a3b8';
          return (
            <rect key={i} x={x} y={0} width={rectW} height={H} fill={color} opacity={0.85} />
          );
        })}
        {sel && (
          <>
            <rect
              x={((sel[0] - tMin) / span) * W}
              y={0}
              width={((sel[1] - sel[0]) / span) * W}
              height={H}
              fill="rgba(99,102,241,0.18)"
              stroke="#4f46e5"
              strokeWidth={1.2}
            />
          </>
        )}
      </svg>
      <div className="flex justify-between text-[10px] text-slate-500 font-mono mt-1">
        <span>{formatTs(tMin)}</span>
        {sel && (
          <span className="text-indigo-700">
            выделено: {formatTs(sel[0])} → {formatTs(sel[1])} ·{' '}
            {Math.round(sel[1] - sel[0])} с
          </span>
        )}
        <span>{formatTs(tMax)}</span>
      </div>
      {!hideLegend && (
        <div className="flex flex-wrap gap-2 mt-2">
          {Object.entries(EMOTION_RU).map(([key, label]) => (
            <span key={key} className="inline-flex items-center gap-1 text-[11px] text-slate-600">
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm"
                style={{ background: EMOTION_COLORS[key as Emotion] }}
              />
              {label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function formatTs(t: number): string {
  if (!t) return '—';
  const d = new Date(t * 1000);
  return d.toLocaleTimeString('ru-RU');
}
