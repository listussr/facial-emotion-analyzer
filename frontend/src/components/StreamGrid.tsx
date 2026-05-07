import { Link } from 'react-router-dom';
import { StreamSource, EMOTION_RU } from '@/types';
import StreamTile from './StreamTile';

interface Props {
  sources: StreamSource[];
  /** Колонок в сетке: 1 / 2 / 3 */
  cols: 1 | 2 | 3;
  /** Префикс роута для перехода в focused-режим */
  detailPathPrefix: '/cameras' | '/uploads';
}

const TRACKER_LABEL = { deepsort: 'DeepSORT', bytetrack: 'ByteTrack' } as const;
const MODEL_LABEL: Record<string, string> = {
  'resnet-18': 'ResNet-18',
  'resnet-18-int8': 'ResNet-18 INT8',
  'resnet-50': 'ResNet-50',
  'resnet-50-int8': 'ResNet-50 INT8',
  convnext: 'ConvNeXt',
  'convnext-int8': 'ConvNeXt INT8',
};

export default function StreamGrid({ sources, cols, detailPathPrefix }: Props) {
  const gridClass =
    cols === 1
      ? 'grid grid-cols-1 gap-5'
      : cols === 2
      ? 'grid grid-cols-1 md:grid-cols-2 gap-5'
      : 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4';

  // В компактной сетке 3×3 убираем подписи в боксах, чтобы было читабельно
  const compact = cols === 3;

  return (
    <section className={gridClass}>
      {sources.map((s) => (
        <Link
          key={s.id}
          to={`${detailPathPrefix}/${s.id}`}
          className="glass p-3 hover:scale-[1.005] transition-transform block"
        >
          <header className="flex items-center justify-between mb-3 px-1">
            <div className="flex items-center gap-2 min-w-0">
              <span className="font-semibold truncate">{s.name}</span>
              {!compact && (
                <span className="chip chip-on text-[11px]">{TRACKER_LABEL[s.tracker]}</span>
              )}
              {!compact && (
                <span className="chip text-[11px]">{MODEL_LABEL[s.model]}</span>
              )}
            </div>
            <span className="text-xs text-slate-500 font-mono shrink-0">
              {s.kind === 'camera'
                ? `${s.fps.toFixed(1)} FPS · ${s.resolution}`
                : `${Math.round(s.progress * 100)}%`}
            </span>
          </header>

          <StreamTile source={s} hideLabels={compact} />

          <div className="mt-3 flex gap-1.5 flex-wrap px-1">
            <span className="chip">
              <span className="font-semibold">{s.faces.length}</span>
              {s.faces.length === 1 ? ' лицо' : ' лиц'}
            </span>
            {!compact && summarizeEmotions(s.faces).map((e) => (
              <span key={e.label} className="chip">
                {e.label}: {e.count}
              </span>
            ))}
            {s.kind === 'camera' && !compact && (
              <span
                className={`chip ${
                  s.latencyMs < 30 ? 'chip-ok' : s.latencyMs < 60 ? '' : 'chip-warn'
                }`}
              >
                задержка {s.latencyMs} мс
              </span>
            )}
          </div>
        </Link>
      ))}
    </section>
  );
}

function summarizeEmotions(faces: { emotion: keyof typeof EMOTION_RU }[]) {
  const counts = new Map<string, number>();
  for (const f of faces) {
    const k = EMOTION_RU[f.emotion];
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  return [...counts.entries()].map(([label, count]) => ({ label, count }));
}
