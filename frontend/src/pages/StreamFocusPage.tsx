import { useParams, Link, useNavigate } from 'react-router-dom';
import { MOCK_CAMERAS, MOCK_UPLOADS } from '@/data/mock';
import StreamTile, { formatTime } from '@/components/StreamTile';
import { EMOTION_RU, StreamSource } from '@/types';

interface Props {
  kind: 'camera' | 'upload';
}

const TRACKER_LABEL = { deepsort: 'DeepSORT', bytetrack: 'ByteTrack' } as const;
const MODEL_LABEL: Record<string, string> = {
  'resnet-18': 'ResNet-18 (FP32)',
  'resnet-18-int8': 'ResNet-18 (INT8)',
  'resnet-50': 'ResNet-50 (FP32)',
  'resnet-50-int8': 'ResNet-50 (INT8)',
  convnext: 'ConvNeXt',
  'convnext-int8': 'ConvNeXt (INT8)',
};

/**
 * Развёрнутый просмотр одного потока. Открывается по клику с сетки.
 * Используется и для камер (/cameras/:id), и для загруженных видео (/uploads/:id).
 */
export default function StreamFocusPage({ kind }: Props) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const list: StreamSource[] = kind === 'camera' ? MOCK_CAMERAS : MOCK_UPLOADS;
  const source = list.find((s) => s.id === id);

  if (!source) {
    return (
      <div className="glass p-10 text-center">
        <h2 className="text-xl font-bold mb-2">Источник не найден</h2>
        <p className="text-slate-600">Возможно, сессия была закрыта.</p>
        <Link
          to={kind === 'camera' ? '/cameras' : '/uploads'}
          className="btn btn-primary mt-4 inline-flex"
        >
          Назад
        </Link>
      </div>
    );
  }

  const back = kind === 'camera' ? '/cameras' : '/uploads';

  return (
    <>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate(back)}
            className="btn btn-ghost text-sm"
            aria-label="Назад"
          >
            <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <line x1="19" y1="12" x2="5" y2="12" />
              <polyline points="12 19 5 12 12 5" />
            </svg>
            {kind === 'camera' ? 'К камерам' : 'К видео'}
          </button>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">{source.name}</h1>
            <p className="text-sm text-slate-500 font-mono">
              {source.kind === 'camera' ? source.url : source.filename} · {source.resolution}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="chip chip-on">{TRACKER_LABEL[source.tracker]}</span>
          <span className="chip">{MODEL_LABEL[source.model]}</span>
          <span className={`chip ${source.device === 'cuda' ? 'chip-on' : ''}`}>
            {source.device.toUpperCase()}
          </span>
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <section className="lg:col-span-2 glass p-4">
          <StreamTile source={source} />

          {source.kind === 'upload' && (
            <div className="mt-4 flex items-center gap-3">
              <button className="btn btn-primary text-sm">
                {source.status === 'paused' ? '▶ Продолжить' : '⏸ Пауза'}
              </button>
              <div className="flex-1">
                <div className="h-2 rounded-full bg-indigo-100 overflow-hidden">
                  <div
                    className="h-full"
                    style={{
                      width: `${source.progress * 100}%`,
                      background: 'linear-gradient(90deg,#4f46e5,#7c3aed,#22d3ee)',
                    }}
                  />
                </div>
                <div className="text-xs text-slate-500 font-mono mt-1 flex justify-between">
                  <span>{formatTime(source.positionSec)}</span>
                  <span>{formatTime(source.durationSec)}</span>
                </div>
              </div>
            </div>
          )}
        </section>

        <aside className="glass p-5">
          <h3 className="font-bold mb-4">Текущие лица</h3>
          {source.faces.length === 0 ? (
            <p className="text-sm text-slate-500">В кадре нет лиц.</p>
          ) : (
            <ul className="space-y-3">
              {source.faces.map((f) => (
                <li key={f.trackId} className="flex items-center gap-3">
                  <span
                    className="w-3 h-3 rounded-full shrink-0"
                    style={{ background: f.color, boxShadow: `0 0 12px ${f.color}` }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm">{f.faceId}</div>
                    <div className="text-xs text-slate-500">
                      {EMOTION_RU[f.emotion]} · уверенность {f.score.toFixed(2)} · трек #{f.trackId}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {source.kind === 'camera' && (
            <>
              <hr className="my-5 border-indigo-100/60" />
              <h3 className="font-bold mb-3">Метрики</h3>
              <ul className="space-y-2 text-sm">
                <Metric label="FPS" value={source.fps.toFixed(1)} />
                <Metric label="Задержка" value={`${source.latencyMs} мс`} />
                <Metric label="Разрешение" value={source.resolution} />
                <Metric
                  label="Состояние"
                  value={
                    source.status === 'live'
                      ? 'в эфире'
                      : source.status === 'slow'
                      ? 'низкий FPS'
                      : 'отключена'
                  }
                />
              </ul>
            </>
          )}
        </aside>
      </div>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <li className="flex justify-between">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono">{value}</span>
    </li>
  );
}
