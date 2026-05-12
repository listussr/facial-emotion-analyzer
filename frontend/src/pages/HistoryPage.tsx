import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/api/client';
import { useUiPrefs } from '@/hooks/useUiPrefs';
import { EMOTION_COLORS, EMOTION_RU, Emotion, SearchMatch } from '@/types';

/**
 * История лиц.
 *
 * Новая логика: пользователь загружает фото человека → бэкенд ищет в БД top-K
 * самых похожих лиц по эмбеддингу → пользователь кликает на нужного и попадает
 * на страницу его истории эмоций.
 *
 * Сколько похожих показывать — настраивается на странице «Настройки»
 * (`searchTopK`, по умолчанию 5).
 */
export default function HistoryPage() {
  const { prefs } = useUiPrefs();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [matches, setMatches] = useState<SearchMatch[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dbUsersCount, setDbUsersCount] = useState<number | null>(null);

  // Узнаём, есть ли вообще что-то в базе лиц. Если нет — показываем пустое
  // состояние с инструкцией, что делать дальше, чтобы поиск был осмысленным.
  useEffect(() => {
    let alive = true;
    api
      .listUsers()
      .then((u) => alive && setDbUsersCount(u.length))
      .catch(() => alive && setDbUsersCount(null));
    return () => {
      alive = false;
    };
  }, []);

  function pickFile(f: File | null) {
    setFile(f);
    setMatches(null);
    setError(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(f ? URL.createObjectURL(f) : null);
  }

  async function runSearch() {
    if (!file) {
      fileRef.current?.click();
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await api.searchByFace(file, prefs.searchTopK);
      setMatches(r);
    } catch (e: any) {
      setError(e?.message || 'Ошибка поиска');
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <div className="mb-6 flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">История по лицу</h1>
          <p className="text-slate-600">
            Загрузите фотографию — система найдёт {prefs.searchTopK} самых похожих лиц
            из базы. Нажмите на нужного, чтобы открыть его историю эмоций.
          </p>
        </div>
      </div>

      {dbUsersCount === 0 && (
        <div className="glass p-6 mb-6 text-center">
          <div
            className="mx-auto w-12 h-12 rounded-full flex items-center justify-center mb-3"
            style={{ background: 'rgba(99,102,241,0.12)', color: '#4338ca' }}
          >
            <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <circle cx="12" cy="8" r="4" />
              <path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2" />
            </svg>
          </div>
          <h3 className="font-bold text-lg">База лиц пуста</h3>
          <p className="text-sm text-slate-600 max-w-md mx-auto mt-1">
            Чтобы здесь появились распознанные люди, обработайте видео на
            странице <Link to="/uploads" className="text-indigo-700 font-medium">Видео</Link>{' '}
            или подключите камеру в{' '}
            <Link to="/settings" className="text-indigo-700 font-medium">Настройках</Link>.
          </p>
        </div>
      )}

      <div className="grid md:grid-cols-3 gap-6 mb-6">
        <section className="glass p-6 md:col-span-2">
          <div
            className="rounded-2xl border-2 border-dashed border-indigo-300/70 p-8 text-center cursor-pointer"
            style={{
              background:
                'linear-gradient(180deg, rgba(99,102,241,0.05), rgba(168,85,247,0.05))',
            }}
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const f = e.dataTransfer.files?.[0];
              if (f) pickFile(f);
            }}
          >
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => pickFile(e.target.files?.[0] || null)}
            />
            {previewUrl ? (
              <div className="flex flex-col items-center gap-3">
                <img
                  src={previewUrl}
                  alt="Загруженное лицо"
                  className="w-40 h-40 object-cover rounded-xl"
                  style={{ boxShadow: '0 12px 36px -16px rgba(79,70,229,0.45)' }}
                />
                <div className="text-sm text-slate-700 font-medium">{file?.name}</div>
                <div className="text-xs text-slate-500">
                  Нажмите кнопку «Найти похожих» или выберите другое фото.
                </div>
              </div>
            ) : (
              <>
                <div
                  className="mx-auto w-14 h-14 rounded-xl flex items-center justify-center text-white"
                  style={{ background: 'linear-gradient(135deg,#4f46e5,#7c3aed)' }}
                >
                  <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                    <rect x="3" y="3" width="18" height="18" rx="2" />
                    <circle cx="8.5" cy="8.5" r="1.5" />
                    <polyline points="21 15 16 10 5 21" />
                  </svg>
                </div>
                <h3 className="mt-3 font-bold">Перетащите изображение</h3>
                <p className="text-sm text-slate-600">JPEG / PNG · одно лицо в кадре</p>
              </>
            )}
          </div>
          {error && <div className="text-sm text-rose-700 mt-3">{error}</div>}
        </section>

        <aside className="glass p-6">
          <h3 className="font-bold mb-3">Параметры</h3>
          <ul className="space-y-2 text-sm">
            <li className="flex justify-between">
              <span className="text-slate-500">Похожих лиц</span>
              <span className="font-mono">{prefs.searchTopK}</span>
            </li>
          </ul>
          <p className="text-xs text-slate-500 mt-3">
            Изменить количество можно на странице «Настройки».
          </p>
          <button
            className="btn btn-primary w-full mt-4 disabled:opacity-50"
            onClick={runSearch}
            disabled={loading}
          >
            {loading ? 'Поиск…' : file ? 'Найти похожих' : 'Выбрать фото'}
          </button>
        </aside>
      </div>

      {matches && (
        <>
          <h2 className="font-bold text-lg mb-4">
            {matches.length === 0 ? 'Совпадений не найдено' : `Найдено: ${matches.length}`}
          </h2>
          {matches.length > 0 && (
            <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
              {matches.map((m) => (
                <Link
                  key={m.user_id}
                  to={`/history/${m.user_id}`}
                  className="glass p-3 hover:scale-[1.02] transition-transform block"
                >
                  <div
                    className="aspect-square rounded-xl overflow-hidden mb-3 relative"
                    style={{
                      background:
                        'linear-gradient(135deg, rgba(99,102,241,0.15), rgba(168,85,247,0.15))',
                    }}
                  >
                    <img
                      src={api.userFaceUrl(m.user_id)}
                      alt=""
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.opacity = '0';
                      }}
                    />
                    <div
                      className="absolute top-2 right-2 px-2 py-0.5 rounded-full text-[10px] font-mono text-white"
                      style={{
                        background: similarityColor(m.similarity),
                        boxShadow: '0 4px 12px -4px rgba(0,0,0,0.25)',
                      }}
                    >
                      {(m.similarity * 100).toFixed(1)}%
                    </div>
                  </div>
                  <div className="text-xs font-mono text-slate-500 truncate">
                    {m.user_id.slice(0, 8)}…
                  </div>
                  <div className="mt-1 flex items-center gap-1.5">
                    {m.dominant_emotion && (
                      <span
                        className="inline-block w-2.5 h-2.5 rounded-full"
                        style={{
                          background:
                            EMOTION_COLORS[m.dominant_emotion as Emotion] || '#94a3b8',
                        }}
                      />
                    )}
                    <span className="text-sm font-semibold">
                      {m.dominant_emotion
                        ? EMOTION_RU[m.dominant_emotion as Emotion] || m.dominant_emotion
                        : '—'}
                    </span>
                  </div>
                  <div className="mt-2 flex justify-between text-[11px] text-slate-500 font-mono">
                    <span>{m.tracks_count} треков</span>
                    <span>{m.total_samples} сэмплов</span>
                  </div>
                </Link>
              ))}
            </section>
          )}
        </>
      )}
    </>
  );
}

function similarityColor(sim: number): string {
  if (sim >= 0.75) return '#10b981'; // зелёный — почти точно тот же
  if (sim >= 0.55) return '#6366f1'; // индиго — близко
  if (sim >= 0.4) return '#f59e0b'; // янтарь — слабо
  return '#94a3b8'; // серый — очень слабо
}
