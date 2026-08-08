import { useMemo, useState } from 'react';
import { TrackHistory } from '@/types';
import {
  EMOTION_ORDER,
  EMOTION_COLORS,
  EMOTION_RU,
  flattenSamples,
  tsRange,
  formatClock,
} from './_chart_utils';

interface Props {
  tracks: TrackHistory[];
  range?: [number, number] | null;
  height?: number;
}

const W = 760;
const PAD_L = 40;
const PAD_R = 12;
const PAD_T = 14;
const PAD_B = 26;

/**
 * Многолинейный график вероятностей: одна линия на каждую из 8 эмоций.
 * Y-ось — [0, 1], X-ось — время. Любую линию можно скрыть кликом по легенде.
 */
export default function EmotionLines({ tracks, range = null, height = 220 }: Props) {
  const samples = useMemo(() => flattenSamples(tracks, range), [tracks, range]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  if (samples.length === 0) {
    return <p className="text-sm text-slate-500">Нет данных для графика.</p>;
  }

  const { tMin, span } = tsRange(samples);
  const H = height;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;

  const xPx = (t: number) => PAD_L + ((t - tMin) / span) * plotW;
  const yPx = (v: number) => PAD_T + (1 - clamp01(v)) * plotH;

  const polylines = EMOTION_ORDER.map((emo, i) => {
    if (hidden.has(emo)) return null;
    const pts = samples.map((s) => `${xPx(s.t).toFixed(1)},${yPx(s.scores[i] ?? 0).toFixed(1)}`).join(' ');
    return (
      <polyline
        key={emo}
        points={pts}
        fill="none"
        stroke={EMOTION_COLORS[emo]}
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity={0.9}
      />
    );
  });

  // Гридлайны по Y
  const yTicks = [0, 0.25, 0.5, 0.75, 1.0];

  return (
    <div className="w-full">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: 'block' }}>
        <rect x={0} y={0} width={W} height={H} fill="rgba(99,102,241,0.04)" rx={8} />
        {yTicks.map((v) => (
          <g key={v}>
            <line
              x1={PAD_L}
              x2={W - PAD_R}
              y1={yPx(v)}
              y2={yPx(v)}
              stroke="rgba(99,102,241,0.15)"
              strokeDasharray="2 4"
            />
            <text
              x={PAD_L - 6}
              y={yPx(v) + 3}
              textAnchor="end"
              fontSize="10"
              fontFamily="monospace"
              fill="#64748b"
            >
              {v.toFixed(2)}
            </text>
          </g>
        ))}
        {polylines}
        {/* Подписи времени */}
        <text x={PAD_L} y={H - 8} fontSize="10" fontFamily="monospace" fill="#64748b">
          {formatClock(samples[0].t)}
        </text>
        <text
          x={W - PAD_R}
          y={H - 8}
          textAnchor="end"
          fontSize="10"
          fontFamily="monospace"
          fill="#64748b"
        >
          {formatClock(samples[samples.length - 1].t)}
        </text>
      </svg>

      <div className="flex flex-wrap gap-2 mt-2">
        {EMOTION_ORDER.map((emo) => {
          const off = hidden.has(emo);
          return (
            <button
              key={emo}
              type="button"
              onClick={() =>
                setHidden((prev) => {
                  const next = new Set(prev);
                  next.has(emo) ? next.delete(emo) : next.add(emo);
                  return next;
                })
              }
              className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-full transition"
              style={{
                background: off ? 'rgba(148,163,184,0.15)' : 'rgba(99,102,241,0.08)',
                color: off ? '#94a3b8' : '#334155',
                textDecoration: off ? 'line-through' : 'none',
              }}
              title={off ? 'Показать линию' : 'Скрыть линию'}
            >
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm"
                style={{ background: EMOTION_COLORS[emo], opacity: off ? 0.4 : 1 }}
              />
              {EMOTION_RU[emo]}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(1, v));
}
