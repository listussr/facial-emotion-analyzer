import { useState } from 'react';
import { MOCK_CAMERAS } from '@/data/mock';
import StreamGrid from '@/components/StreamGrid';
import GridSelector, { GridCols } from '@/components/GridSelector';

/**
 * Сетка только живых камер. Загруженные файлы здесь не показываем —
 * они на отдельной странице /uploads.
 */
export default function CamerasPage() {
  const [cols, setCols] = useState<GridCols>(2);
  const cameras = MOCK_CAMERAS;

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

      {cameras.length === 0 ? (
        <div className="glass p-10 text-center text-slate-500">
          Нет подключённых камер. Добавьте источник в разделе «Настройки».
        </div>
      ) : (
        <StreamGrid sources={cameras} cols={cols} detailPathPrefix="/cameras" />
      )}

      <p className="text-xs text-slate-500 mt-6 text-center">
        Нажмите на любой тайл, чтобы развернуть его на весь экран.
      </p>
    </>
  );
}
