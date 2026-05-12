import { useMemo } from 'react';
import { TrackHistory } from '@/types';
import {
  EMOTION_ORDER,
  EMOTION_COLORS,
  EMOTION_RU,
  flattenSamples,
} from './_chart_utils';

interface Props {
  tracks: TrackHistory[];
  range?: [number, number] | null;
  /** Радиус «лепестка» в пикселях. */
  size?: number;
}

/**
 * Эмоциональный профиль — средняя вероятность каждой из 8 эмоций как
 * polar/radar chart. Хорошо показывает «характер» человека по сессиям:
 * например, доминирующая ось «sad» означает, что лицо устойчиво грустное,
 * а равномерный многоугольник — нейтральное.
 */
export default function EmotionRadar({ tracks, range = null, size = 320 }: Props) {
  const samples = useMemo(() => flattenSamples(tracks, range), [tracks, range]);

  if (samples.length === 0) {
    return <p className="text-sm text-slate-500">Нет данных для профиля.</p>;
  }

  // Средние вероятности по каждой эмоции
  const N = samples.length;
  const means = EMOTION_ORDER.map((_, i) => {
    let s = 0;
    for (const sm of samples) s += clamp01(sm.scores[i] ?? 0);
    return s / N;
  });
  const maxMean = Math.max(...means, 0.001);

  const cx = size / 2;
  const cy = size / 2;
  const R = size * 0.38;

  // Углы для 8 осей — начиная с верха (-π/2), по часовой
  const angle = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / EMOTION_ORDER.length;
  const point = (i: number, r: number) => {
    const a = angle(i);
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)] as const;
  };

  // Полигон полигона значений (нормируем к [0, R]). Используем относительную
  // нормализацию к максимальному значению — иначе при тихих эмоциях фигура
  // получается слипшейся в центре.
  const polyPoints = means
    .map((v, i) => {
      const r = (v / maxMean) * R;
      const [x, y] = point(i, r);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  // Концентрические кольца (0.25 / 0.5 / 0.75 / 1.0 от R) + оси
  const rings = [0.25, 0.5, 0.75, 1.0];

  return (
    <div className="w-full flex flex-col items-center">
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
        {rings.map((k) => {
          const pts = EMOTION_ORDER.map((_, i) => {
            const [x, y] = point(i, R * k);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
          }).join(' ');
          return (
            <polygon
              key={k}
              points={pts}
              fill="none"
              stroke="rgba(99,102,241,0.15)"
              strokeWidth={1}
            />
          );
        })}
        {/* Оси */}
        {EMOTION_ORDER.map((_, i) => {
          const [x, y] = point(i, R);
          return (
            <line
              key={i}
              x1={cx}
              y1={cy}
              x2={x}
              y2={y}
              stroke="rgba(99,102,241,0.18)"
              strokeWidth={1}
            />
          );
        })}
        {/* Сама фигура */}
        <polygon
          points={polyPoints}
          fill="rgba(99,102,241,0.25)"
          stroke="#6366f1"
          strokeWidth={2}
          strokeLinejoin="round"
        />
        {/* Точки на вершинах */}
        {means.map((v, i) => {
          const r = (v / maxMean) * R;
          const [x, y] = point(i, r);
          return (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={3.5}
              fill={EMOTION_COLORS[EMOTION_ORDER[i]]}
              stroke="white"
              strokeWidth={1.5}
            />
          );
        })}
        {/* Подписи эмоций по периметру */}
        {EMOTION_ORDER.map((emo, i) => {
          const [x, y] = point(i, R + 18);
          return (
            <text
              key={emo}
              x={x}
              y={y}
              textAnchor={Math.abs(Math.cos(angle(i))) < 0.2 ? 'middle' : Math.cos(angle(i)) < 0 ? 'end' : 'start'}
              dominantBaseline="middle"
              fontSize="11"
              fill="#334155"
            >
              {EMOTION_RU[emo]}
            </text>
          );
        })}
      </svg>

      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs mt-3 font-mono">
        {EMOTION_ORDER.map((emo, i) => (
          <div key={emo} className="flex items-center justify-between gap-3">
            <span className="flex items-center gap-1.5">
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm"
                style={{ background: EMOTION_COLORS[emo] }}
              />
              {EMOTION_RU[emo]}
            </span>
            <span className="text-slate-500">{(means[i] * 100).toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(1, v));
}
