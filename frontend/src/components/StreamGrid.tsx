import { Link } from 'react-router-dom';
import { StreamSource } from '@/types';
import StreamTile from './StreamTile';
import { api } from '@/api/client';

interface Props {
  sources: StreamSource[];
  cols: 1 | 2 | 3;
  detailPathPrefix: '/cameras' | '/uploads';
  /** Если true — показываем реальные MJPEG-стримы, а не плейсхолдеры */
  live?: boolean;
  /** Колбэк остановки сессии (показывается крестик в углу) */
  onStop?: (id: string) => void;
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

export default function StreamGrid({
  sources,
  cols,
  detailPathPrefix,
  live = false,
  onStop,
}: Props) {
  const gridClass =
    cols === 1
      ? 'grid grid-cols-1 gap-5'
      : cols === 2
      ? 'grid grid-cols-1 md:grid-cols-2 gap-5'
      : 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4';

  const compact = cols === 3;

  return (
    <section className={gridClass}>
      {sources.map((s) => (
        <div key={s.id} className="glass p-3 hover:scale-[1.005] transition-transform relative">
          {onStop && (
            <button
              onClick={(e) => {
                e.preventDefault();
                onStop(s.id);
              }}
              className="absolute top-2 right-2 z-20 w-7 h-7 rounded-full bg-rose-500/85 hover:bg-rose-500 text-white flex items-center justify-center"
              title="Остановить сессию"
              aria-label="Остановить"
            >
              <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4}>
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}

          <Link to={`${detailPathPrefix}/${s.id}`} className="block">
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
                  ? s.fps > 0
                    ? `${s.fps.toFixed(1)} FPS · ${s.resolution}`
                    : s.status
                  : `${Math.round(s.progress * 100)}%`}
              </span>
            </header>

            <StreamTile source={s} liveSrc={live ? api.streamUrl(s.id) : undefined} hideLabels={compact} />

            <div className="mt-3 flex gap-1.5 flex-wrap px-1">
              {s.kind === 'camera' && (
                <span
                  className={`chip ${
                    s.status === 'live'
                      ? 'chip-ok'
                      : s.status === 'slow'
                      ? 'chip-warn'
                      : s.status === 'error'
                      ? 'chip-warn'
                      : ''
                  }`}
                >
                  {s.status === 'live'
                    ? 'в эфире'
                    : s.status === 'slow'
                    ? 'низкий FPS'
                    : s.status === 'error'
                    ? 'ошибка источника'
                    : 'офлайн'}
                </span>
              )}
              {s.kind === 'upload' && <span className="chip">{uploadStatusLabel(s.status)}</span>}
            </div>
          </Link>
        </div>
      ))}
    </section>
  );
}

function uploadStatusLabel(status: string) {
  switch (status) {
    case 'queued':
      return 'в очереди';
    case 'running':
      return 'обрабатывается';
    case 'paused':
      return 'на паузе';
    case 'done':
      return 'готово';
    default:
      return status;
  }
}
