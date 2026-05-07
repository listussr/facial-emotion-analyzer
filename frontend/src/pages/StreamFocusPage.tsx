import { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import StreamTile, { formatTime } from '@/components/StreamTile';
import { EMOTION_RU, StreamSource, Emotion } from '@/types';
import { api } from '@/api/client';
import { useSessionEvents } from '@/hooks/useSessionEvents';

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
 * Развёрнутый просмотр одной сессии. Тянет данные через GET /api/sessions
 * + WebSocket /api/events/{id} для лайв-аналитики.
 */
export default function StreamFocusPage({ kind }: Props) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [source, setSource] = useState<StreamSource | null>(null);
  const [error, setError] = useState<string | null>(null);

  const back = kind === 'camera' ? '/cameras' : '/uploads';

  // Опрос данных о сессии (для статуса/имени)
  useEffect(() => {
    if (!id) return;
    let alive = true;
    const fetchOne = async () => {
      try {
        const list = await api.listSessions(kind);
        if (!alive) return;
        const s = list.find((x) => x.id === id);
        if (!s) {
          setError('Сессия не найдена');
        } else {
          setSource(s);
          setError(null);
        }
      } catch (e: any) {
        if (alive) setError(e?.message || 'fetch error');
      }
    };
    fetchOne();
    const t = window.setInterval(fetchOne, 3000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [id, kind]);

  const events = useSessionEvents(id);

  if (error || !source) {
    return (
      <div className="glass p-10 text-center">
        <h2 className="text-xl font-bold mb-2">{error ? error : 'Загрузка…'}</h2>
        {error && (
          <Link to={back} className="btn btn-primary mt-4 inline-flex">
            Назад
          </Link>
        )}
      </div>
    );
  }

  const liveFaces = events.latest?.faces ?? [];

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
          <span className={`chip ${events.connected ? 'chip-ok' : 'chip-warn'}`}>
            <span
              className="pulse-dot"
              style={{ background: events.connected ? '#10b981' : '#f59e0b' }}
            />
            {events.connected ? 'WS активен' : 'WS отключён'}
          </span>
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <section className="lg:col-span-2 glass p-4">
          <StreamTile source={source} liveSrc={api.streamUrl(source.id)} />

          {source.kind === 'upload' && (
            <div className="mt-4 flex items-center gap-3">
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
          {liveFaces.length === 0 ? (
            <p className="text-sm text-slate-500">
              {events.connected ? 'В кадре нет лиц.' : 'Ожидаем данные аналитики…'}
            </p>
          ) : (
            <ul className="space-y-3">
              {liveFaces.map((f) => {
                const emo = (f.emotion as Emotion) || 'neutral';
                return (
                  <li key={f.track_id} className="flex items-center gap-3">
                    <span
                      className="w-3 h-3 rounded-full shrink-0"
                      style={{ background: '#7c3aed', boxShadow: '0 0 12px #7c3aed' }}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-sm">{f.face_id}</div>
                      <div className="text-xs text-slate-500">
                        {f.emotion ? EMOTION_RU[emo] : '—'} · трек #{f.track_id}
                        {f.emotion_scores.length > 0 && (
                          <> · уверенность {Math.max(...f.emotion_scores).toFixed(2)}</>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          <hr className="my-5 border-indigo-100/60" />
          <h3 className="font-bold mb-3">Метрики</h3>
          <ul className="space-y-2 text-sm">
            <Metric label="FPS аналитики" value={events.fps > 0 ? events.fps.toFixed(1) : '—'} />
            <Metric label="Лиц в кадре" value={String(liveFaces.length)} />
            <Metric label="Frame ID" value={String(events.latest?.frame_id ?? '—')} />
            <Metric label="Состояние" value={statusLabel(source)} />
          </ul>
        </aside>
      </div>
    </>
  );
}

function statusLabel(s: StreamSource): string {
  if (s.kind === 'camera') {
    return s.status === 'live'
      ? 'в эфире'
      : s.status === 'slow'
      ? 'низкий FPS'
      : s.status === 'error'
      ? 'ошибка источника'
      : 'отключена';
  }
  switch (s.status) {
    case 'queued':
      return 'в очереди';
    case 'running':
      return 'обрабатывается';
    case 'paused':
      return 'на паузе';
    case 'done':
      return 'готово';
    default:
      return s.status;
  }
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <li className="flex justify-between">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono">{value}</span>
    </li>
  );
}
