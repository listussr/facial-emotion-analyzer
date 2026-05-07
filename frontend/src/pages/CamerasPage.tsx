import { useState } from 'react';
import { Link } from 'react-router-dom';
import StreamGrid from '@/components/StreamGrid';
import GridSelector, { GridCols } from '@/components/GridSelector';
import { useSessions } from '@/hooks/useSessions';

export default function CamerasPage() {
  const [cols, setCols] = useState<GridCols>(2);
  const { sessions, loading, error, stop } = useSessions('camera');

  return (
    <>
      <div className="flex items-end justify-between mb-6 flex-wrap gap-4">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">Камеры</h1>
          <p className="text-slate-600">Подключённые в реальном времени видеопотоки.</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500">Сетка:</span>
          <GridSelector value={cols} onChange={setCols} />
        </div>
      </div>

      {error && (
        <div className="glass p-4 mb-4 text-sm text-rose-700">
          Ошибка соединения с бэкендом: {error}
        </div>
      )}

      {loading && sessions.length === 0 ? (
        <div className="glass p-10 text-center text-slate-500">Загрузка…</div>
      ) : sessions.length === 0 ? (
        <div className="glass p-10 text-center">
          <p className="text-slate-600 mb-4">
            Нет подключённых камер. Добавьте источник в разделе настроек.
          </p>
          <Link to="/settings" className="btn btn-primary inline-flex">
            Перейти в настройки
          </Link>
        </div>
      ) : (
        <StreamGrid
          sources={sessions}
          cols={cols}
          detailPathPrefix="/cameras"
          live
          onStop={(id) => stop(id)}
        />
      )}

      <p className="text-xs text-slate-500 mt-6 text-center">
        Нажмите на любой тайл, чтобы развернуть его на весь экран.
      </p>
    </>
  );
}
