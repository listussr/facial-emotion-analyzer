import { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import StreamTile, { formatTime } from '@/components/StreamTile';
import EmotionBars from '@/components/EmotionBars';
import {
  EMOTION_RU, StreamSource, Emotion, SessionHistoryDetail, TrackHistory, HistoryModality, VoiceHistory,
} from '@/types';
import { api } from '@/api/client';
import { useSessionEvents, SceneVoice } from '@/hooks/useSessionEvents';
import { useLiveFaceStats, FaceStats } from '@/hooks/useLiveFaceStats';
import { MODEL_LABEL, FUSION_LABEL, DEFAULT_FUSION_METHOD } from '@/data/models';
import { MODALITY_LABEL } from '@/components/ProcessingOptions';

const HISTORY_MODALITY_LABEL: Record<HistoryModality, string> = { face: 'Лицо', fused: 'Итог (лицо + голос)' };

interface Props {
  kind: 'camera' | 'upload';
}

const TRACKER_LABEL = { deepsort: 'DeepSORT', bytetrack: 'ByteTrack' } as const;

/**
 * Развёрнутый просмотр одной сессии. Тянет данные через GET /api/sessions
 * + WebSocket /api/events/{id} для лайв-аналитики.
 */
export default function StreamFocusPage({ kind }: Props) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [source, setSource] = useState<StreamSource | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statsOn, setStatsOn] = useState(false);

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

  // Per-face статистика по WebSocket-событиям — копится клиентом,
  // обновляется раз в секунду (см. хук). Эти данные «живые» — они
  // отражают то, что бэкенд видит прямо сейчас в кадре, а не агрегацию из БД.
  const faceStats = useLiveFaceStats(events.latest);

  // Поллинг истории сессии из БД — нужен, чтобы видеть треки, которые
  // уже завершились (особенно важно после конца загруженного видео,
  // когда WS больше не присылает новых событий).
  const [dbHistory, setDbHistory] = useState<SessionHistoryDetail | null>(null);
  const [voiceHistory, setVoiceHistory] = useState<VoiceHistory | null>(null);
  const [histModality, setHistModality] = useState<HistoryModality>('face');
  useEffect(() => {
    if (!statsOn || !id) {
      setDbHistory(null);
      setVoiceHistory(null);
      return;
    }
    let alive = true;
    const fetchDb = async () => {
      try {
        const [r, v] = await Promise.all([
          api.sessionHistory(id, histModality),
          api.sessionVoice(id).catch(() => null),
        ]);
        if (alive) {
          setDbHistory(r);
          setVoiceHistory(v);
        }
      } catch {
        /* молча — раздел всё равно покажет live-данные */
      }
    };
    fetchDb();
    const t = window.setInterval(fetchDb, 5000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [id, statsOn, histModality]);

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
  const sceneVoice = events.latest?.scene_voice ?? null;
  const active = source.modalities && source.modalities.length ? source.modalities : source.requestedModalities || [];
  const hasAudio = active.includes('audio');
  const hasVideo = active.includes('video');

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
          <span
            className={`chip ${source.degraded ? 'chip-warn' : 'chip-on'}`}
            title={source.degraded
              ? `Запрошено: ${(source.requestedModalities || []).map((m) => MODALITY_LABEL[m]).join(' + ')}; источник дал только это`
              : 'Модальности сессии'}
          >
            {active.map((m) => MODALITY_LABEL[m]).join(' + ') || '—'}
            {source.degraded ? ' (не всё)' : ''}
          </span>
          {hasVideo && <span className="chip chip-on">{TRACKER_LABEL[source.tracker]}</span>}
          {hasVideo && <span className="chip">{MODEL_LABEL[source.model] || source.model}</span>}
          {hasVideo && source.clipFrames && <span className="chip">{source.clipFrames} кадров/окно</span>}
          {hasVideo && hasAudio && (
            <span className="chip" title="Метод слияния лица и голоса">
              {FUSION_LABEL[source.fusionMethod || DEFAULT_FUSION_METHOD]}
            </span>
          )}
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
          <button
            type="button"
            className={`btn ${statsOn ? 'btn-primary' : 'btn-ghost'} text-sm`}
            onClick={() => setStatsOn((v) => !v)}
          >
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M12 20v-6" />
              <path d="M6 20V10" />
              <path d="M18 20V4" />
            </svg>
            {statsOn ? 'Скрыть статистику' : 'Показать статистику'}
          </button>
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <section className="lg:col-span-2 glass p-4">
          <StreamTile source={source} live showStats />

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
                const emo = f.emotion as Emotion | null;
                // оценка была, но неуверенная (порог 0.4) — «не определено»
                const undetermined = !emo && f.emotion_scores.length > 0;
                return (
                  <li key={f.track_id} className="flex items-center gap-3">
                    <span
                      className="w-3 h-3 rounded-full shrink-0"
                      style={{ background: '#7c3aed', boxShadow: '0 0 12px #7c3aed' }}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-sm">{f.face_id}</div>
                      <div className="text-xs text-slate-500">
                        {emo ? EMOTION_RU[emo] : undetermined ? 'не определено' : 'оценка копится…'} · трек #{f.track_id}
                        {f.emotion_scores.length > 0 && (
                          <> · уверенность {Math.max(...f.emotion_scores).toFixed(2)}</>
                        )}
                      </div>
                      {f.face_emotion !== undefined && (
                        <div className="text-[11px] text-slate-400">
                          лицо: {f.face_emotion ? EMOTION_RU[f.face_emotion] : '—'}
                          {f.voice_attributed && (
                            <>
                              {' '}· голос: {f.voice_emotion ? EMOTION_RU[f.voice_emotion] : '—'}
                            </>
                          )}
                          {f.fusion_used && (
                            <span title="Что реально слито в последнем окне"> · {describeFusion(f.fusion_used)}</span>
                          )}
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          {hasAudio && (
            <>
              <hr className="my-5 border-indigo-100/60" />
              <h3 className="font-bold mb-3">Голос</h3>
              <VoicePanel voice={sceneVoice} connected={events.connected} />
            </>
          )}

          <hr className="my-5 border-indigo-100/60" />
          <h3 className="font-bold mb-3">Метрики</h3>
          <ul className="space-y-2 text-sm">
            <Metric
              label="FPS источника"
              value={source.fps > 0 ? source.fps.toFixed(1) : '—'}
            />
            <Metric
              label="FPS аналитики"
              value={events.fps > 0 ? events.fps.toFixed(1) : '—'}
            />
            {hasVideo && <Metric label="Лиц в кадре" value={String(liveFaces.length)} />}
            {hasAudio && (
              <Metric label="Звука отправлено" value={`${(source.audioSeconds ?? 0).toFixed(1)} с`} />
            )}
            {source.errors > 0 && (
              <Metric label="Ошибки" value={String(source.errors)} />
            )}
            {source.kind === 'upload' && source.durationSec > 0 && (
              <Metric
                label="Позиция"
                value={`${formatTime(source.positionSec)} / ${formatTime(source.durationSec)}`}
              />
            )}
            {source.kind === 'upload' && (
              <Metric label="Прогресс" value={`${Math.round(source.progress * 100)}%`} />
            )}
            <Metric label="Состояние" value={statusLabel(source)} />
          </ul>
        </aside>
      </div>

      {/* Per-face статистика. Включается кнопкой выше. Доступна только в
          режиме одиночного просмотра (1×1). Показываем сразу два среза:
            — «Сейчас в кадре»: накапливается с WebSocket в реальном времени;
            — «Завершённые треки»: подтягиваем из БД, чтобы видеть и тех,
              кто уже ушёл из кадра, и оставшиеся записи после конца видео. */}
      {statsOn && (
        <section className="glass p-5 mt-6">
          <header className="flex items-center justify-between mb-4 flex-wrap gap-2">
            <h3 className="font-bold text-lg">Статистика по лицам</h3>
            <div className="flex items-center gap-3 flex-wrap">
              <div className="text-xs text-slate-500 font-mono flex flex-wrap gap-3">
                <span>
                  WS:{' '}
                  <span style={{ color: events.connected ? '#10b981' : '#dc2626' }}>
                    {events.connected ? 'активен' : 'отключён'}
                  </span>
                </span>
                <span>в кадре: {faceStats.length}</span>
                {dbHistory && (
                  <span>в БД: {dbHistory.tracks_count} треков · {dbHistory.unique_users} лиц</span>
                )}
              </div>
              {(dbHistory?.available_modalities?.length ?? 0) > 1 && (
                <div className="seg">
                  {(['face', 'fused'] as HistoryModality[]).map((m) => (
                    <button
                      key={m}
                      type="button"
                      className={histModality === m ? 'active' : ''}
                      onClick={() => setHistModality(m)}
                    >
                      {HISTORY_MODALITY_LABEL[m]}
                    </button>
                  ))}
                </div>
              )}
              {dbHistory && dbHistory.tracks_count > 0 && (
                <a
                  href={api.sessionExportCsvUrl(source.id)}
                  download
                  className="btn btn-ghost text-xs py-1 px-2"
                  title="Экспортировать сэмплы сессии в CSV"
                >
                  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="7 10 12 15 17 10" />
                    <line x1="12" y1="15" x2="12" y2="3" />
                  </svg>
                  CSV
                </a>
              )}
            </div>
          </header>

          <h4 className="font-semibold mb-3">Сейчас в кадре</h4>
          {faceStats.length === 0 ? (
            <p className="text-sm text-slate-500 mb-6">
              {events.connected
                ? 'Ожидаем первые лица в кадре…'
                : 'WebSocket не подключён — события аналитики не приходят.'}
            </p>
          ) : (
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
              {faceStats.map((s) => (
                <FaceStatsCard key={s.key} stats={s} />
              ))}
            </div>
          )}

          <h4 className="font-semibold mb-3">Завершённые треки этой сессии</h4>
          {!dbHistory || dbHistory.tracks_count === 0 ? (
            <p className="text-sm text-slate-500">
              Пока ничего не записано в БД. Запись появится, когда трек уйдёт
              из кадра (через ~5 секунд бездействия).
            </p>
          ) : (
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
              {dbHistory.tracks.map((t, i) => (
                <DbTrackCard key={`${t.camera_id}:${t.track_id}:${i}`} track={t} userId={(t as any).user_id} />
              ))}
            </div>
          )}

          {hasAudio && (
            <>
              <h4 className="font-semibold mt-6 mb-3">Голос сессии</h4>
              {!voiceHistory || voiceHistory.total_windows === 0 ? (
                <p className="text-sm text-slate-500">
                  Голос сохраняется в БД по окончании сессии (окна с речью).
                </p>
              ) : (
                <div className="md:w-1/2">
                  <div className="text-[11px] text-slate-500 mb-2">
                    окон с речью: {voiceHistory.total_windows}
                  </div>
                  <EmotionBars counts={voiceHistory.label_counts} total={voiceHistory.total_windows} />
                </div>
              )}
            </>
          )}
        </section>
      )}
    </>
  );
}

function DbTrackCard({ track, userId }: { track: TrackHistory; userId?: string }) {
  const counts: Record<string, number> = {};
  for (const s of track.samples) {
    counts[s.label] = (counts[s.label] || 0) + 1;
  }
  const total = track.samples.length;
  const dur =
    track.started_at && track.ended_at
      ? Math.max(0, Math.round(track.ended_at - track.started_at))
      : 0;
  return (
    <div className="rounded-xl p-4 border" style={{ borderColor: 'rgba(99,102,241,0.2)' }}>
      <header className="flex items-center justify-between mb-3 gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold">трек #{track.track_id}</div>
          <div className="text-[11px] text-slate-500">
            {dur} с · {total} сэмплов
          </div>
        </div>
        {userId && (
          <Link to={`/history/${userId}`} className="btn btn-ghost text-[11px] py-1 px-2">
            История →
          </Link>
        )}
      </header>
      <EmotionBars counts={counts} total={total || 1} />
    </div>
  );
}

function FaceStatsCard({ stats }: { stats: FaceStats }) {
  const isPending = stats.face_id.startsWith('pending-');
  const totalSamples = stats.samples.length;
  const cur = stats.current_label as Emotion | null;

  return (
    <div className="rounded-xl p-4 border" style={{ borderColor: 'rgba(99,102,241,0.2)' }}>
      <header className="flex items-center justify-between mb-3 gap-2">
        <div className="min-w-0">
          <div className="font-mono text-xs text-slate-500 truncate">
            {isPending ? '…ожидаем идентификацию…' : stats.face_id.slice(0, 8) + '…'}
          </div>
          <div className="text-sm font-semibold">
            трек #{stats.track_id}
            {cur && (
              <span className="ml-2 text-slate-500">
                · сейчас: {EMOTION_RU[cur] || cur}
              </span>
            )}
          </div>
        </div>
        {!isPending ? (
          <Link
            to={`/history/${stats.face_id}`}
            className="btn btn-ghost text-[11px] py-1 px-2"
            title="Открыть полную историю"
          >
            История →
          </Link>
        ) : null}
      </header>

      <div className="text-[11px] text-slate-500 mb-1">
        собрано сэмплов: {totalSamples}
      </div>
      <EmotionBars counts={stats.label_counts} total={totalSamples || 1} />
    </div>
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
      return String((s as { status?: string }).status ?? '');
  }
}

/** 'stack_hidden -> video_only (нет аудио)' → понятная подпись */
function describeFusion(used: string): string {
  if (used.includes('video_only')) return used.startsWith('video_only') ? 'только лицо' : 'только лицо (нет голоса)';
  if (used.includes('audio_only')) return used.startsWith('audio_only') ? 'только голос' : 'только голос (нет окна лица)';
  return 'лицо + голос';
}

function VoicePanel({ voice, connected }: { voice: SceneVoice | null; connected: boolean }) {
  if (!voice) {
    return (
      <p className="text-sm text-slate-500">
        {connected ? 'Речи пока не было.' : 'Ожидаем данные аналитики…'}
      </p>
    );
  }
  const fresh = voice.age_ms < 3000;
  return (
    <div className="flex items-center gap-3">
      <span
        className="pulse-dot"
        style={{ background: voice.speaking ? '#10b981' : '#94a3b8' }}
        title={voice.speaking ? 'говорят' : 'тишина'}
      />
      <div className="flex-1 min-w-0">
        <div className="font-medium text-sm">{voice.label ? EMOTION_RU[voice.label] : 'не определено'}</div>
        <div className="text-xs text-slate-500">
          {voice.speaking ? 'говорят' : 'тишина'} · оценке {(voice.age_ms / 1000).toFixed(1)} с
          {!fresh && ' (устарела)'}
          {voice.scores.length > 0 && <> · уверенность {Math.max(...voice.scores).toFixed(2)}</>}
        </div>
      </div>
    </div>
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
