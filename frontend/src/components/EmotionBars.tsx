import { EMOTION_RU, EMOTION_COLORS, Emotion } from '@/types';

interface Props {
  /** Кол-во сэмплов на каждую эмоцию */
  counts: Record<string, number>;
  total?: number;
}

/**
 * Горизонтальные бар-чарты распределения эмоций — без сторонних зависимостей,
 * подходит и для истории по пользователю, и для статистики сессии.
 */
export default function EmotionBars({ counts, total }: Props) {
  const sum = total ?? Object.values(counts).reduce((a, b) => a + b, 0);
  if (sum === 0) {
    return <p className="text-sm text-slate-500">Нет данных.</p>;
  }
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);

  return (
    <ul className="space-y-2">
      {entries.map(([label, count]) => {
        const pct = (count / sum) * 100;
        const emo = label as Emotion;
        const color = EMOTION_COLORS[emo] || '#94a3b8';
        const ru = EMOTION_RU[emo] || label;
        return (
          <li key={label}>
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="font-medium">{ru}</span>
              <span className="font-mono text-slate-500">
                {count} · {pct.toFixed(1)}%
              </span>
            </div>
            <div className="h-2 rounded-full bg-indigo-100/60 overflow-hidden">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${pct}%`,
                  background: `linear-gradient(90deg, ${color}, ${color}aa)`,
                }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
