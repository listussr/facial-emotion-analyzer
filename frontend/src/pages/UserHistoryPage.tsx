import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '@/api/client';
import { TrackHistory, UserHistoryDetail } from '@/types';
import EmotionBars from '@/components/EmotionBars';
import EmotionTimeline from '@/components/EmotionTimeline';
import EmotionLines from '@/components/charts/EmotionLines';
import EmotionAreaChart from '@/components/charts/EmotionAreaChart';
import EmotionRadar from '@/components/charts/EmotionRadar';

export default function UserHistoryPage() {
  const { userId } = useParams<{ userId: string }>();
  const [data, setData] = useState<UserHistoryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<[number, number] | null>(null);

  useEffect(() => {
    if (!userId) return;
    let alive = true;
    api
      .userHistory(userId)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e?.message || 'fetch failed'));
    return () => {
      alive = false;
    };
  }, [userId]);

  if (error) {
    return (
      <div className="glass p-10 text-center">
        <h2 className="text-xl font-bold mb-2">Не удалось получить историю</h2>
        <p className="text-slate-600">{error}</p>
        <Link to="/history" className="btn btn-primary mt-4 inline-flex">
          Назад
        </Link>
      </div>
    );
  }

  if (!data) {
    return <div className="glass p-10 text-center text-slate-500">Загрузка…</div>;
  }

  const totalSeconds = data.tracks.reduce((acc, t) => {
    if (!t.started_at || !t.ended_at) return acc;
    return acc + (t.ended_at - t.started_at);
  }, 0);
  const cameras = Array.from(
    new Set(data.tracks.map((t) => t.camera_id).filter(Boolean) as string[])
  );

  return (
    <>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Link to="/history" className="btn btn-ghost text-sm">
            <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <line x1="19" y1="12" x2="5" y2="12" />
              <polyline points="12 19 5 12 12 5" />
            </svg>
            К списку
          </Link>
          <div>
            <h1 className="text-2xl font-extrabold tracking-tight">Профиль человека</h1>
            <p className="text-sm text-slate-500 font-mono">{data.user_id}</p>
          </div>
        </div>
        <a
          href={api.userExportCsvUrl(data.user_id)}
          download
          className="btn btn-ghost text-sm"
          title="Экспортировать все сэмплы в CSV"
        >
          <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          Скачать CSV
        </a>
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <section className="glass p-5 lg:col-span-1">
          <div
            className="aspect-square rounded-xl overflow-hidden mb-4"
            style={{
              background:
                'linear-gradient(135deg, rgba(99,102,241,0.15), rgba(168,85,247,0.15))',
            }}
          >
            <img
              src={api.userFaceUrl(data.user_id)}
              alt=""
              className="w-full h-full object-cover"
            />
          </div>
          <ul className="space-y-2 text-sm">
            <Stat label="Всего треков" value={data.tracks_count} />
            <Stat label="Всего сэмплов" value={data.total_samples} />
            <Stat label="Время в кадре" value={`${Math.round(totalSeconds)} с`} />
            <Stat label="Камер" value={cameras.length} />
          </ul>
        </section>

        <section className="glass p-5 lg:col-span-2">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-bold text-lg">Распределение эмоций</h3>
            {range && (
              <span className="chip chip-on text-[11px]">в выделенном окне</span>
            )}
          </div>
          <ZoomableBars data={data} range={range} />
        </section>
      </div>

      <section className="glass p-5 mt-6">
        <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
          <h3 className="font-bold text-lg">Таймлайн (все треки)</h3>
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <span>Кликните и перетащите — выделите участок для зума.</span>
            {range && (
              <button
                className="btn btn-ghost text-xs py-1 px-2"
                onClick={() => setRange(null)}
              >
                Сбросить зум
              </button>
            )}
          </div>
        </div>
        <EmotionTimeline
          tracks={data.tracks}
          height={70}
          onRangeSelect={setRange}
          selectedRange={range}
        />
        {range && (
          <div className="mt-5">
            <h4 className="font-semibold mb-2 text-sm">Зум выделенного диапазона</h4>
            <ZoomedTimeline data={data} range={range} />
          </div>
        )}
      </section>

      {/* Подробные графики вероятностей — учитывают выделенный диапазон */}
      <section className="glass p-5 mt-6">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="font-bold text-lg">Графики вероятностей</h3>
          {range && (
            <span className="chip chip-on text-[11px]">в выделенном окне</span>
          )}
        </div>
        <ChartSwitcher tracks={data.tracks} range={range} />
      </section>

      <section className="glass p-5 mt-6">
        <h3 className="font-bold text-lg mb-4">Отдельные треки</h3>
        {data.tracks.length === 0 ? (
          <p className="text-sm text-slate-500">У этого пользователя нет таймсерий.</p>
        ) : (
          <ul className="divide-y divide-indigo-100/60">
            {data.tracks.map((t, i) => (
              <TrackRow key={i} track={t} />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <li className="flex justify-between">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono">{value}</span>
    </li>
  );
}

/**
 * Фильтрует треки/сэмплы по выделенному диапазону. Используется в обоих
 * зум-производных вьюшках (бары распределения и развёрнутый таймлайн).
 */
function filterTracksByRange(
  tracks: TrackHistory[],
  range: [number, number] | null
): TrackHistory[] {
  if (!range) return tracks;
  const [s, e] = range;
  return tracks
    .map((t) => ({ ...t, samples: t.samples.filter((x) => x.t >= s && x.t <= e) }))
    .filter((t) => t.samples.length > 0);
}

function ZoomableBars({
  data,
  range,
}: {
  data: UserHistoryDetail;
  range: [number, number] | null;
}) {
  const { counts, total } = useMemo(() => {
    if (!range) {
      return { counts: data.label_counts, total: data.total_samples };
    }
    const filtered = filterTracksByRange(data.tracks, range);
    const c: Record<string, number> = {};
    for (const t of filtered) {
      for (const s of t.samples) {
        c[s.label] = (c[s.label] || 0) + 1;
      }
    }
    return { counts: c, total: Object.values(c).reduce((a, b) => a + b, 0) };
  }, [data, range]);

  return <EmotionBars counts={counts} total={total} />;
}

function TrackRow({ track }: { track: TrackHistory }) {
  const [open, setOpen] = useState(false);
  const dur =
    track.started_at && track.ended_at
      ? Math.round(track.ended_at - track.started_at)
      : 0;
  const counts: Record<string, number> = {};
  for (const s of track.samples) {
    counts[s.label] = (counts[s.label] || 0) + 1;
  }
  return (
    <li className="py-3">
      <button
        type="button"
        className="w-full flex items-center gap-3 flex-wrap text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <span
          className={`inline-flex items-center justify-center w-5 h-5 rounded-md text-[11px] font-bold transition-transform ${
            open ? 'rotate-90' : ''
          }`}
          style={{ background: 'rgba(99,102,241,0.12)', color: '#4338ca' }}
        >
          ›
        </span>
        <span className="text-xs text-slate-500 font-mono">
          {new Date(track.created_at).toLocaleString('ru-RU')}
        </span>
        <span className="chip">{track.camera_id || '—'}</span>
        <span className="chip">трек #{track.track_id}</span>
        <span className="chip">длит. {dur} с</span>
        <span className="chip">{track.samples.length} сэмплов</span>
      </button>
      {open && (
        <>
          <div className="mt-3 grid lg:grid-cols-2 gap-5">
            <div>
              <h4 className="font-semibold mb-2 text-sm">Распределение эмоций</h4>
              <EmotionBars counts={counts} total={track.samples.length || 1} />
            </div>
            <div>
              <h4 className="font-semibold mb-2 text-sm">Таймлайн трека</h4>
              <EmotionTimeline tracks={[track]} height={70} hideLegend />
            </div>
          </div>
          <div className="mt-4">
            <h4 className="font-semibold mb-2 text-sm">Графики вероятностей</h4>
            <ChartSwitcher tracks={[track]} range={null} />
          </div>
        </>
      )}
    </li>
  );
}

type ChartKind = 'lines' | 'area' | 'radar';

const CHART_TABS: { value: ChartKind; label: string; hint: string }[] = [
  { value: 'lines', label: 'Линии', hint: 'Вероятности каждой эмоции во времени' },
  { value: 'area',  label: 'Области', hint: 'Стэкированные вероятности по моментам' },
  { value: 'radar', label: 'Профиль', hint: 'Средние вероятности (polar)' },
];

function ChartSwitcher({
  tracks,
  range,
}: {
  tracks: TrackHistory[];
  range: [number, number] | null;
}) {
  const [kind, setKind] = useState<ChartKind>('lines');
  return (
    <>
      <div className="seg mb-4">
        {CHART_TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            className={kind === t.value ? 'active' : ''}
            onClick={() => setKind(t.value)}
            title={t.hint}
          >
            {t.label}
          </button>
        ))}
      </div>
      {kind === 'lines' && <EmotionLines tracks={tracks} range={range} />}
      {kind === 'area' && <EmotionAreaChart tracks={tracks} range={range} />}
      {kind === 'radar' && (
        <div className="flex justify-center">
          <EmotionRadar tracks={tracks} range={range} />
        </div>
      )}
      <p className="text-xs text-slate-500 mt-3">
        {CHART_TABS.find((t) => t.value === kind)?.hint}
      </p>
    </>
  );
}

function ZoomedTimeline({
  data,
  range,
}: {
  data: UserHistoryDetail;
  range: [number, number];
}) {
  const filtered = useMemo(
    () => filterTracksByRange(data.tracks, range),
    [data.tracks, range]
  );
  if (filtered.length === 0) {
    return <p className="text-sm text-slate-500">В выделенном окне нет данных.</p>;
  }
  return <EmotionTimeline tracks={filtered} height={90} hideLegend />;
}
