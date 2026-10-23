import { StreamSource } from '@/types';
import LiveVideo from '@/components/live/LiveVideo';

interface Props {
  source: StreamSource;
  /** Реальное видео сессии: кадры и аналитика по WebSocket, рамки рисует браузер. */
  live?: boolean;
  /** Скрыть подписи на рамках (маленькие превью) */
  hideLabels?: boolean;
  /** FPS показа и задержка в углу */
  showStats?: boolean;
}

export function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export default function StreamTile({ source, live = false, hideLabels = false, showStats = false }: Props) {
  const running = source.kind === 'camera' ? source.status === 'live' || source.status === 'slow' : source.status === 'running';

  return (
    <div className="live-tile w-full">
      {live && (
        <LiveVideo
          sessionId={source.id}
          hideLabels={hideLabels}
          showStats={showStats}
          className="absolute inset-0 w-full h-full object-cover"
        />
      )}

      {running && (
        <span className="absolute top-2 left-2 z-10 inline-flex items-center gap-1.5 rounded-full bg-black/55 px-2 py-0.5 text-[11px] font-medium text-white">
          <span className="pulse-dot" style={{ background: '#ef4444' }} />
          {source.kind === 'camera' ? 'в эфире' : 'обработка'}
        </span>
      )}

      {source.kind === 'upload' && source.durationSec > 0 && (
        <>
          <div className="absolute bottom-2 left-2 right-2 h-1 rounded-full bg-white/20 overflow-hidden z-10">
            <div className="h-full bg-indigo-400" style={{ width: `${source.progress * 100}%` }} />
          </div>
          <div className="absolute bottom-4 right-2 text-[11px] font-mono text-white/80 z-10">
            {formatTime(source.positionSec)} / {formatTime(source.durationSec)}
          </div>
        </>
      )}
    </div>
  );
}
