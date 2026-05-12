import { useMemo } from 'react';
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
 * Stacked area chart — для каждого момента вертикально складываются
 * вероятности 8 эмоций. Дискретная картина в каждом моменте видна как
 * вертикальный «срез», цвета снизу вверх в порядке EMOTION_ORDER.
 *
 * Нормализуем сумму к 1 на случай, если из-за округлений она чуть-чуть
 * не сошлась — визуально это удобнее, чем «обрезанная» сверху картинка.
 */
export default function EmotionAreaChart({ tracks, range = null, height = 220 }: Props) {
  const samples = useMemo(() => flattenSamples(tracks, range), [tracks, range]);

  if (samples.length < 2) {
    return <p className="text-sm text-slate-500">Нужно минимум 2 сэмпла для области.</p>;
  }

  const { tMin, span } = tsRange(samples);
  const H = height;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;
  const xPx = (t: number) => PAD_L + ((t - tMin) / span) * plotW;
  const yPx = (v: number) => PAD_T + (1 - clamp01(v)) * plotH;

  // На каждый сэмпл — нормализованные кумулятивные значения [0..1] для каждой эмоции.
  const cums = samples.map((s) => {
    const scores = s.scores;
    const sum = scores.reduce((a, b) => a + Math.max(0, b || 0), 0) || 1;
    const norm = scores.map((v) => Math.max(0, v || 0) / sum);
    const acc: number[] = [];
    let c = 0;
    for (const v of norm) {
      c += v;
      acc.push(c);
    }
    return acc;
  });

  // Полигоны: для каждой эмоции — лента между cum[i-1] и cum[i] по всем сэмплам
  const polygons = EMOTION_ORDER.map((emo, i) => {
    const topPts = samples.map((s, k) => `${xPx(s.t).toFixed(1)},${yPx(cums[k][i]).toFixed(1)}`);
    const bottomPts = samples.map((s, k) => {
      const v = i === 0 ? 0 : cums[k][i - 1];
      return `${xPx(s.t).toFixed(1)},${yPx(v).toFixed(1)}`;
    });
    // Полигон: top L→R, потом bottom R→L
    const points = [...topPts, ...bottomPts.reverse()].join(' ');
    return (
      <polygon
        key={emo}
        points={points}
        fill={EMOTION_COLORS[emo]}
        opacity={0.9}
        stroke="none"
      />
    );
  });

  return (
    <div className="w-full">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: 'block' }}>
        <rect x={0} y={0} width={W} height={H} fill="rgba(99,102,241,0.04)" rx={8} />
        {polygons}
        {/* подписи Y */}
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <text
              x={PAD_L - 6}
              y={yPx(v) + 3}
              textAnchor="end"
              fontSize="10"
              fontFamily="monospace"
              fill="#64748b"
            >
              {v.toFixed(1)}
            </text>
          </g>
        ))}
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
        {EMOTION_ORDER.map((emo) => (
          <span
            key={emo}
            className="inline-flex items-center gap-1 text-[11px] text-slate-600"
          >
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm"
              style={{ background: EMOTION_COLORS[emo] }}
            />
            {EMOTION_RU[emo]}
          </span>
        ))}
      </div>
    </div>
  );
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(1, v));
}
