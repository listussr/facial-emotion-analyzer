import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '@/api/client';
import EmotionBars from '@/components/EmotionBars';
import { EMOTION_RU, RecordingInfo, RecordingStatus } from '@/types';

export const RECORDING_STATUS_LABEL: Record<RecordingStatus, string> = {
  recording: 'идёт запись',
  rendering: 'собирается видео',
  ready: 'готова',
  failed: 'ошибка',
};

const STATUS_CHIP: Record<RecordingStatus, string> = {
  recording: 'chip-on',
  rendering: 'chip-warn',
  ready: 'chip-ok',
  failed: 'chip-warn',
};

const PENDING: RecordingStatus[] = ['recording', 'rendering'];

function formatDuration(sec?: number | null): string {
  if (sec == null) return '—';
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatSize(bytes?: number | null): string {
  if (bytes == null) return '—';
  return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} МБ` : `${Math.round(bytes / 1e3)} КБ`;
}

function formatDate(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString('ru-RU') : '—';
}

export default function RecordingsPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [items, setItems] = useState<RecordingInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setItems(await api.listRecordings());
      setError(null);
    } catch (e: any) {
      setError(e?.message || 'Не удалось загрузить записи');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const pending = items?.some((r) => PENDING.includes(r.status));
  useEffect(() => {
    if (!pending) return;
    const t = window.setInterval(refresh, 3000);
    return () => window.clearInterval(t);
  }, [pending, refresh]);

  const selected = items?.find((r) => r.session_id === id) ?? null;

  async function remove(rec: RecordingInfo) {
    if (!window.confirm(`Удалить запись «${rec.name || rec.session_id}»?`)) return;
    try {
      await api.deleteRecording(rec.session_id);
      if (rec.session_id === id) navigate('/recordings');
      await refresh();
    } catch (e: any) {
      setError(e?.message || 'Не удалось удалить запись');
    }
  }

  return (
    <>
      <div className="mb-6">
        <h1 className="text-3xl font-extrabold tracking-tight">Записи сессий</h1>
        <p className="text-slate-600">
          После окончания сессии сервер собирает итоговое видео с рамками слитых эмоций лица и голоса
          и сохраняет его вместе с аналитикой в хранилище.
        </p>
      </div>

      {error && <div className="glass p-4 mb-4 text-sm text-red-700">{error}</div>}

      {selected && <RecordingView rec={selected} onDelete={() => remove(selected)} />}

      {items && items.length === 0 && (
        <div className="glass p-6 text-center text-slate-600">
          Записей пока нет. Запустите{' '}
          <Link to="/" className="text-indigo-700 font-medium">новый анализ</Link> — запись появится после его окончания.
        </div>
      )}

      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {items?.map((rec) => (
          <Link
            key={rec.session_id}
            to={`/recordings/${rec.session_id}`}
            className={`glass p-4 block hover:scale-[1.01] transition-transform ${
              rec.session_id === id ? 'ring-2 ring-indigo-400' : ''
            }`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="font-bold truncate">{rec.name || rec.session_id}</div>
                <div className="text-xs text-slate-500 font-mono truncate">{rec.session_id}</div>
              </div>
              <span className={`chip ${STATUS_CHIP[rec.status]}`}>{RECORDING_STATUS_LABEL[rec.status]}</span>
            </div>
            <div className="mt-3 text-sm text-slate-600 flex flex-wrap gap-x-4 gap-y-1">
              <span>{formatDate(rec.started_at)}</span>
              <span>{formatDuration(rec.duration_sec)}</span>
              <span>{formatSize(rec.size_bytes)}</span>
              {rec.kind && <span>{rec.kind === 'camera' ? 'камера' : 'файл'}</span>}
            </div>
            {rec.summary?.fused_label_counts && <DominantLine counts={rec.summary.fused_label_counts} />}
          </Link>
        ))}
      </div>
    </>
  );
}

function DominantLine({ counts }: { counts: Record<string, number> }) {
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  if (!top) return null;
  return (
    <div className="mt-2 text-sm">
      Чаще всего: <b>{EMOTION_RU[top[0] as keyof typeof EMOTION_RU] || top[0]}</b>
    </div>
  );
}

function RecordingView({ rec, onDelete }: { rec: RecordingInfo; onDelete: () => void }) {
  const ready = rec.status === 'ready';
  // ?t=секунды — ссылка на момент записи (например, из разбора агента)
  const [params] = useSearchParams();
  const start = Number(params.get('t'));
  const tracks = Object.entries(rec.summary?.tracks || {});
  return (
    <section className="glass p-6 mb-6">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h2 className="text-xl font-bold">{rec.name || rec.session_id}</h2>
          <p className="text-sm text-slate-500">
            {formatDate(rec.started_at)} · {formatDuration(rec.duration_sec)} · {rec.frames ?? 0} кадров
            {rec.has_audio ? ' · со звуком' : ' · без звука'}
            {rec.truncated ? ' · обрезана по лимиту длительности' : ''}
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          {ready && (
            <>
              <a className="btn btn-primary text-sm" href={api.recordingVideoUrl(rec.session_id, true)}>
                Скачать видео
              </a>
              <a className="btn btn-ghost text-sm" href={api.recordingAnalyticsUrl(rec.session_id)}>
                Аналитика JSON
              </a>
            </>
          )}
          {!PENDING.includes(rec.status) && (
            <button type="button" className="btn btn-ghost text-sm" onClick={onDelete}>
              Удалить
            </button>
          )}
        </div>
      </div>

      {ready ? (
        <video
          key={rec.session_id}
          className="w-full max-h-[70vh] rounded-xl bg-black"
          src={api.recordingVideoUrl(rec.session_id)}
          controls
          preload="metadata"
          onLoadedMetadata={(e) => {
            if (Number.isFinite(start) && start > 0) e.currentTarget.currentTime = start;
          }}
        />
      ) : rec.status === 'failed' ? (
        <p className="text-sm text-red-700">Не удалось собрать запись: {rec.error || 'неизвестная ошибка'}</p>
      ) : (
        <p className="text-sm text-slate-600">
          {RECORDING_STATUS_LABEL[rec.status]} — страница обновится, когда запись будет готова.
        </p>
      )}

      {ready && rec.summary && (
        <div className="grid md:grid-cols-3 gap-6 mt-6">
          <div>
            <h3 className="font-semibold mb-2">Итог (лицо + голос)</h3>
            <EmotionBars counts={rec.summary.fused_label_counts || {}} />
          </div>
          <div>
            <h3 className="font-semibold mb-2">Только лицо</h3>
            <EmotionBars counts={rec.summary.face_label_counts || {}} />
          </div>
          <div>
            <h3 className="font-semibold mb-2">Голос в кадре</h3>
            <EmotionBars counts={rec.summary.voice_label_counts || {}} />
          </div>
          {tracks.length > 0 && (
            <div className="md:col-span-3 text-sm text-slate-600">
              Треков: {tracks.length}.{' '}
              {tracks
                .slice(0, 8)
                .map(([t, v]) => `#${t}: ${EMOTION_RU[v.dominant] || v.dominant}`)
                .join(', ')}
              {tracks.length > 8 ? '…' : ''}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
