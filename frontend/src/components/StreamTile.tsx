import { CSSProperties } from 'react';
import { StreamSource, EMOTION_RU } from '@/types';

interface Props {
  source: StreamSource;
  /**
   * Если задан, рендерим реальный MJPEG-стрим через <img>. Картинка уже
   * содержит bbox-ы и подписи (их рисует visualizer.py), так что сверху
   * декоративные оверлеи не нужны.
   */
  liveSrc?: string;
  /** Скрыть подписи на bbox в плейсхолдере (для маленьких тайлов 3×3) */
  hideLabels?: boolean;
}

const TRACKER_LABEL = { deepsort: 'DeepSORT', bytetrack: 'ByteTrack' } as const;

export function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export default function StreamTile({ source, liveSrc, hideLabels = false }: Props) {
  const isLive = Boolean(liveSrc);

  return (
    <div className="live-tile w-full">
      {/* Реальный MJPEG. Если стрим ещё не подключён, остаётся фон-плейсхолдер. */}
      {isLive && (
        <img
          src={liveSrc}
          alt={source.name}
          className="absolute inset-0 w-full h-full object-cover"
        />
      )}

      <div className="scanline" />

      {/* Декоративные bbox-ы только в режиме без реального стрима (мок/preview) */}
      {!isLive &&
        source.faces.map((f, i) => {
          const [x1, y1, x2, y2] = f.bbox;
          const style: CSSProperties = {
            top: `${y1}%`,
            left: `${x1}%`,
            width: `${x2 - x1}%`,
            height: `${y2 - y1}%`,
            border: `2px solid ${f.color}`,
            color: f.color,
          };
          return (
            <div key={i} className="live-bbox" style={style}>
              {!hideLabels && (
                <div
                  className="absolute -top-6 left-0 text-[11px] font-mono px-1.5 py-0.5 rounded text-slate-900"
                  style={{ background: f.color, opacity: 0.92 }}
                >
                  {EMOTION_RU[f.emotion]} {f.score.toFixed(2)} · {f.faceId}
                </div>
              )}
            </div>
          );
        })}

      {/* Бейджи поверх стрима */}
      <div className="absolute top-2 left-2 flex items-center gap-1.5 z-10">
        <span
          className="pulse-dot"
          style={{
            background:
              source.kind === 'camera' && source.status === 'live' ? '#22d3ee' : '#a78bfa',
          }}
        />
        <span className="chip chip-on text-[10px]">{source.id}</span>
      </div>
      <div className="absolute top-2 right-2 flex items-center gap-1.5 z-10">
        <span className="chip text-[10px] font-mono">{TRACKER_LABEL[source.tracker]}</span>
      </div>

      {source.kind === 'upload' && (
        <>
          <div className="absolute bottom-2 left-2 right-2 h-1.5 rounded-full bg-white/15 overflow-hidden z-10">
            <div
              className="h-full"
              style={{
                width: `${source.progress * 100}%`,
                background: 'linear-gradient(90deg,#a78bfa,#22d3ee)',
              }}
            />
          </div>
          <div className="absolute bottom-4 left-2 text-[10px] font-mono text-white/80 z-10">
            {source.fps > 0 ? `${source.fps.toFixed(1)} FPS` : ''}
          </div>
          <div className="absolute bottom-4 right-2 text-[10px] font-mono text-white/80 z-10">
            {formatTime(source.positionSec)} / {formatTime(source.durationSec)}
          </div>
        </>
      )}

      {source.kind === 'camera' && (
        <div className="absolute bottom-2 right-2 text-[10px] font-mono text-white/80 z-10">
          {source.fps > 0 ? `${source.fps.toFixed(1)} FPS` : '—'}
        </div>
      )}
    </div>
  );
}
