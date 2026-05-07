import { CSSProperties } from 'react';
import { StreamSource, EMOTION_RU } from '@/types';

interface Props {
  source: StreamSource;
  /** Если задано — рендерим только overlay поверх плейсхолдера, без header/footer */
  compact?: boolean;
  /** Скрыть подписи на bbox (для маленьких тайлов в больших сетках) */
  hideLabels?: boolean;
}

const TRACKER_LABEL = { deepsort: 'DeepSORT', bytetrack: 'ByteTrack' } as const;

export function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/**
 * Тайл с потоковым видео + аннотациями. На текущем этапе показывает
 * стилизованный плейсхолдер. После подключения backend замените содержимое
 * .live-tile на <img src={`/api/stream/${source.id}`} /> или <video>.
 */
export default function StreamTile({ source, hideLabels = false }: Props) {
  return (
    <div className="live-tile w-full">
      <div className="scanline" />

      {/* TODO: подключить реальный стрим — это плейсхолдер с боксами */}
      {source.faces.map((f, i) => {
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

      {/* Бейджи внутри тайла */}
      <div className="absolute top-2 left-2 flex items-center gap-1.5">
        <span
          className="pulse-dot"
          style={{
            background: source.kind === 'camera' && source.status === 'live' ? '#22d3ee' : '#a78bfa',
          }}
        />
        <span className="chip chip-on text-[10px]">{source.id}</span>
      </div>
      <div className="absolute top-2 right-2 flex items-center gap-1.5">
        <span className="chip text-[10px] font-mono">{TRACKER_LABEL[source.tracker]}</span>
      </div>

      {/* Footer для upload — прогресс и таймкод */}
      {source.kind === 'upload' && (
        <>
          <div className="absolute bottom-2 left-2 right-2 h-1.5 rounded-full bg-white/15 overflow-hidden">
            <div
              className="h-full"
              style={{
                width: `${source.progress * 100}%`,
                background: 'linear-gradient(90deg,#a78bfa,#22d3ee)',
              }}
            />
          </div>
          <div className="absolute bottom-4 right-2 text-[10px] font-mono text-white/80">
            {formatTime(source.positionSec)} / {formatTime(source.durationSec)}
          </div>
        </>
      )}

      {/* Footer для камеры — fps */}
      {source.kind === 'camera' && (
        <div className="absolute bottom-2 right-2 text-[10px] font-mono text-white/80">
          {source.fps.toFixed(1)} FPS
        </div>
      )}
    </div>
  );
}
