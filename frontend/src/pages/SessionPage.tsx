import { ReactNode, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import StreamTile, { formatTime } from '@/components/StreamTile';
import EmotionBars from '@/components/EmotionBars';
import RecordingChip from '@/components/RecordingChip';
import {
  EMOTION_COLORS, EMOTION_RU, Emotion, HistoryModality, SessionHistoryDetail, StreamSource, TrackHistory, VoiceHistory,
} from '@/types';
import { api } from '@/api/client';
import { useSessionEvents, SceneVoice } from '@/hooks/useSessionEvents';
import { useLiveFaceStats, FaceStats } from '@/hooks/useLiveFaceStats';
import { FUSION_LABEL, MODEL_LABEL, VOICE_MODELS, DEFAULT_FUSION_METHOD } from '@/data/models';
import { isActive, statusText } from '@/data/sessionStatus';

const HISTORY_MODALITY_LABEL: Record<HistoryModality, string> = { face: 'Только лицо', fused: 'Лицо + голос' };
const MODALITY_LABEL = { video: 'лицо', audio: 'голос' } as const;
const TRACKER_LABEL = { deepsort: 'DeepSORT', bytetrack: 'ByteTrack' } as const;
const VOICE_LABEL: Record<string, string> = Object.fromEntries(VOICE_MODELS.map((m) => [m.value, m.label]));

export default function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [source, setSource] = useState<StreamSource | null>(null);
  const [missing, setMissing] = useState(false);
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    const fetchOne = async () => {
      try {
        const s = (await api.listSessions()).find((x) => x.id === id);
        if (!alive) return;
        setSource(s ?? null);
        setMissing(!s);
      } catch {
        if (alive) setMissing(false);
      }
    };
    fetchOne();
    const t = window.setInterval(fetchOne, 3000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [id]);

  const events = useSessionEvents(id);

  if (!source) {
    return (
      <div className="glass p-10 text-center">
        {missing ? (
          <>
            <h2 className="text-xl font-bold mb-2">Сессия уже не активна</h2>
            <p className="text-slate-600 mb-4">Итоговое видео и статистика сохранены в записях.</p>
            <div className="flex justify-center gap-2">
              <Link to={`/recordings/${id}`} className="btn btn-primary">
                Открыть запись
              </Link>
              <Link to="/" className="btn btn-ghost">
                К анализу
              </Link>
            </div>
          </>
        ) : (
          <h2 className="text-xl font-bold">Загрузка…</h2>
        )}
      </div>
    );
  }

  const active = isActive(source);
  const modalities = source.modalities?.length ? source.modalities : source.requestedModalities || [];
  const hasAudio = modalities.includes('audio');
  const hasVideo = modalities.includes('video');
  const faces = events.latest?.faces ?? [];

  async function stop() {
    setStopping(true);
    try {
      await api.stopSession(source!.id);
      navigate('/');
    } finally {
      setStopping(false);
    }
  }

  return (
    <>
      <Link to="/" className="text-sm text-slate-500 hover:text-slate-800">
        ← К анализу
      </Link>
      <div className="flex items-center justify-between gap-3 flex-wrap mt-2 mb-6">
        <div className="min-w-0">
          <h1 className="text-2xl font-extrabold tracking-tight truncate">{source.name}</h1>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <span className={`chip ${active ? 'chip-live' : source.status === 'error' ? 'chip-warn' : ''}`}>
              {active && <span className="pulse-dot" style={{ background: '#ef4444' }} />}
              {statusText(source)}
            </span>
            <span className="chip">{modalities.map((m) => MODALITY_LABEL[m]).join(' + ') || '—'}</span>
            {source.degraded && (
              <span className="chip chip-warn" title="В источнике нет части выбранных дорожек">
                не все дорожки
              </span>
            )}
            <RecordingChip sessionId={source.id} />
          </div>
        </div>
        {active && (
          <button type="button" className="btn btn-danger" onClick={stop} disabled={stopping}>
            {stopping ? 'Останавливаем…' : 'Остановить'}
          </button>
        )}
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <section className="lg:col-span-2">
          {hasVideo ? (
            <StreamTile source={source} live />
          ) : (
            <div className="glass p-10 text-center text-slate-500">Анализируется только голос — видео нет.</div>
          )}
        </section>

        <aside className="space-y-6">
          {hasVideo && (
            <div className="glass p-5">
              <h3 className="font-bold mb-3">Сейчас в кадре</h3>
              {faces.length === 0 ? (
                <p className="text-sm text-slate-500">
                  {!active ? 'Анализ завершён.' : events.connected ? 'Лиц не видно.' : 'Подключаемся…'}
                </p>
              ) : (
                <ul className="space-y-4">
                  {faces.map((f, i) => (
                    <FaceRow
                      key={f.track_id}
                      index={i + 1}
                      emotion={f.emotion as Emotion | null}
                      scores={f.emotion_scores}
                      face={f.face_emotion as Emotion | null | undefined}
                      voice={f.voice_attributed ? (f.voice_emotion as Emotion | null) : undefined}
                    />
                  ))}
                </ul>
              )}
            </div>
          )}

          {hasAudio && (
            <div className="glass p-5">
              <h3 className="font-bold mb-3">Голос</h3>
              <VoicePanel voice={events.latest?.scene_voice ?? null} connected={events.connected} active={active} />
            </div>
          )}
        </aside>
      </div>

      <Collapsible title="Статистика по людям" subtitle="распределение эмоций за сессию и история каждого человека">
        <SessionStats sessionId={source.id} hasAudio={hasAudio} latest={events.latest} connected={events.connected} />
      </Collapsible>

      <Collapsible title="Технические подробности">
        <dl className="grid sm:grid-cols-2 gap-x-8 gap-y-2 text-sm">
          <Detail label="Источник" value={source.kind === 'camera' ? source.url : source.filename} />
          {source.kind === 'upload' && source.durationSec > 0 && (
            <Detail label="Позиция" value={`${formatTime(source.positionSec)} / ${formatTime(source.durationSec)}`} />
          )}
          <Detail label="Кадров в секунду (источник)" value={source.fps > 0 ? source.fps.toFixed(1) : '—'} />
          <Detail label="Кадров в секунду (анализ)" value={events.fps > 0 ? events.fps.toFixed(1) : '—'} />
          {hasVideo && <Detail label="Модель лица" value={MODEL_LABEL[source.model] || source.model} />}
          {hasAudio && <Detail label="Модель голоса" value={VOICE_LABEL[source.voiceModel || ''] || source.voiceModel || '—'} />}
          {hasVideo && hasAudio && (
            <Detail label="Объединение лица и голоса" value={FUSION_LABEL[source.fusionMethod || DEFAULT_FUSION_METHOD]} />
          )}
          {hasVideo && <Detail label="Кадров на окно" value={String(source.clipFrames ?? '—')} />}
          {hasVideo && <Detail label="Трекер" value={TRACKER_LABEL[source.tracker]} />}
          <Detail label="Вычисления" value={source.device.toUpperCase()} />
          {hasAudio && <Detail label="Звука получено" value={`${(source.audioSeconds ?? 0).toFixed(1)} с`} />}
          <Detail label="Канал событий" value={events.connected ? 'подключён' : 'нет связи'} />
          {source.errors > 0 && <Detail label="Ошибки" value={String(source.errors)} />}
          <Detail label="Идентификатор" value={source.id} />
        </dl>
      </Collapsible>
    </>
  );
}

function FaceRow({
  index,
  emotion,
  scores,
  face,
  voice,
}: {
  index: number;
  emotion: Emotion | null;
  scores: number[];
  face?: Emotion | null;
  voice?: Emotion | null;
}) {
  const confidence = scores.length ? Math.max(...scores) : 0;
  const color = emotion ? EMOTION_COLORS[emotion] : '#94a3b8';
  return (
    <li>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full" style={{ background: color }} />
          <span className="font-semibold">{emotion ? EMOTION_RU[emotion] : scores.length ? 'не определено' : 'оценивается…'}</span>
        </div>
        <span className="hint">человек {index}</span>
      </div>
      {emotion && (
        <div className="h-1.5 rounded-full bg-slate-100 mt-2 overflow-hidden">
          <div className="h-full rounded-full" style={{ width: `${confidence * 100}%`, background: color }} />
        </div>
      )}
      {face !== undefined && (
        <div className="hint mt-1">
          по лицу: {face ? EMOTION_RU[face] : '—'}
          {voice !== undefined && <> · по голосу: {voice ? EMOTION_RU[voice] : '—'}</>}
        </div>
      )}
    </li>
  );
}

function VoicePanel({ voice, connected, active }: { voice: SceneVoice | null; connected: boolean; active: boolean }) {
  if (!voice || !active) {
    return (
      <p className="text-sm text-slate-500">
        {!active ? 'Анализ завершён.' : connected ? 'Речи пока не было.' : 'Подключаемся…'}
      </p>
    );
  }
  return (
    <div className="flex items-center gap-3">
      <span className="pulse-dot" style={{ background: voice.speaking ? '#10b981' : '#94a3b8' }} />
      <div>
        <div className="font-semibold">{voice.label ? EMOTION_RU[voice.label] : 'не определено'}</div>
        <div className="hint">{voice.speaking ? 'кто-то говорит' : 'тишина'}</div>
      </div>
    </div>
  );
}

function Collapsible({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="glass mt-6">
      <button type="button" className="w-full flex items-center justify-between gap-3 p-5 text-left" onClick={() => setOpen((v) => !v)}>
        <div>
          <div className="font-bold">{title}</div>
          {subtitle && <div className="hint">{subtitle}</div>}
        </div>
        <span className="text-slate-400 text-xl">{open ? '−' : '+'}</span>
      </button>
      {open && <div className="px-5 pb-5">{children}</div>}
    </section>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-slate-100 py-1">
      <dt className="text-slate-500">{label}</dt>
      <dd className="font-medium text-right break-all">{value}</dd>
    </div>
  );
}

function SessionStats({
  sessionId,
  hasAudio,
  latest,
  connected,
}: {
  sessionId: string;
  hasAudio: boolean;
  latest: ReturnType<typeof useSessionEvents>['latest'];
  connected: boolean;
}) {
  const faceStats = useLiveFaceStats(latest);
  const [history, setHistory] = useState<SessionHistoryDetail | null>(null);
  const [voice, setVoice] = useState<VoiceHistory | null>(null);
  const [modality, setModality] = useState<HistoryModality>('fused');

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [h, v] = await Promise.all([
          api.sessionHistory(sessionId, modality),
          api.sessionVoice(sessionId).catch(() => null),
        ]);
        if (alive) {
          setHistory(h);
          setVoice(v);
        }
      } catch {
        return;
      }
    };
    load();
    const t = window.setInterval(load, 5000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [sessionId, modality]);

  const modalities = history?.available_modalities ?? [];
  return (
    <div className="space-y-6">
      {faceStats.length > 0 && (
        <div>
          <h4 className="font-semibold mb-3">Сейчас в кадре</h4>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
            {faceStats.map((s) => (
              <LiveFaceCard key={s.key} stats={s} />
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
          <h4 className="font-semibold">Люди этой сессии</h4>
          <div className="flex items-center gap-2">
            {modalities.length > 1 && (
              <div className="seg">
                {(['fused', 'face'] as HistoryModality[]).map((m) => (
                  <button key={m} type="button" className={modality === m ? 'active' : ''} onClick={() => setModality(m)}>
                    {HISTORY_MODALITY_LABEL[m]}
                  </button>
                ))}
              </div>
            )}
            {history && history.tracks_count > 0 && (
              <a href={api.sessionExportCsvUrl(sessionId)} download className="btn btn-ghost text-sm py-1.5">
                Скачать CSV
              </a>
            )}
          </div>
        </div>
        {!history || history.tracks_count === 0 ? (
          <p className="text-sm text-slate-500">
            {connected
              ? 'Человек попадает сюда через несколько секунд после того, как уходит из кадра.'
              : 'Пока никого не сохранено.'}
          </p>
        ) : (
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
            {history.tracks.map((t, i) => (
              <TrackCard key={`${t.track_id}:${i}`} track={t} userId={(t as any).user_id} index={i + 1} />
            ))}
          </div>
        )}
      </div>

      {hasAudio && voice && voice.total_windows > 0 && (
        <div className="md:w-1/2">
          <h4 className="font-semibold mb-3">Голос за сессию</h4>
          <EmotionBars counts={voice.label_counts} total={voice.total_windows} />
        </div>
      )}
    </div>
  );
}

function TrackCard({ track, userId, index }: { track: TrackHistory; userId?: string; index: number }) {
  const counts: Record<string, number> = {};
  for (const s of track.samples) counts[s.label] = (counts[s.label] || 0) + 1;
  const dur = track.started_at && track.ended_at ? Math.max(0, Math.round(track.ended_at - track.started_at)) : 0;
  return (
    <div className="rounded-xl p-4 border border-slate-200">
      <header className="flex items-center justify-between mb-3 gap-2">
        <div>
          <div className="font-semibold">Человек {index}</div>
          <div className="hint">в кадре {dur} с</div>
        </div>
        {userId && (
          <Link to={`/history/${userId}`} className="text-sm text-indigo-700 font-medium">
            Вся история →
          </Link>
        )}
      </header>
      <EmotionBars counts={counts} total={track.samples.length || 1} />
    </div>
  );
}

function LiveFaceCard({ stats }: { stats: FaceStats }) {
  const pending = stats.face_id.startsWith('pending-');
  return (
    <div className="rounded-xl p-4 border border-slate-200">
      <header className="flex items-center justify-between mb-3 gap-2">
        <div className="font-semibold">
          {stats.current_label ? EMOTION_RU[stats.current_label as Emotion] : 'оценивается…'}
        </div>
        {!pending && (
          <Link to={`/history/${stats.face_id}`} className="text-sm text-indigo-700 font-medium">
            История →
          </Link>
        )}
      </header>
      <EmotionBars counts={stats.label_counts} total={stats.samples.length || 1} />
    </div>
  );
}
