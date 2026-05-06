import { useState, useRef } from 'react';
import { MOCK_UPLOADS } from '@/data/mock';
import StreamGrid from '@/components/StreamGrid';
import GridSelector, { GridCols } from '@/components/GridSelector';
import Toggle from '@/components/Toggle';

const TRACKERS = [
  { value: 'deepsort', label: 'DeepSORT' },
  { value: 'bytetrack', label: 'ByteTrack' },
];
const MODELS = [
  { value: 'resnet-18', label: 'ResNet-18 (FP32)' },
  { value: 'resnet-18-int8', label: 'ResNet-18 (INT8)' },
  { value: 'resnet-50', label: 'ResNet-50 (FP32)' },
  { value: 'convnext', label: 'ConvNeXt' },
];

/**
 * Страница «Видео»: загрузка файлов + сетка обрабатываемых файлов
 * (по аналогии с /cameras, но строго отделена от живых камер).
 */
export default function UploadsPage() {
  const [cols, setCols] = useState<GridCols>(2);
  const [tracker, setTracker] = useState('deepsort');
  const [model, setModel] = useState('resnet-18');
  const [device, setDevice] = useState<'cpu' | 'cuda'>('cpu');
  const fileRef = useRef<HTMLInputElement>(null);

  const uploads = MOCK_UPLOADS;

  return (
    <>
      <div className="flex items-end justify-between mb-6 flex-wrap gap-4">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">Видео</h1>
          <p className="text-slate-600">
            Загрузите файл и наблюдайте за обработкой. Можно открывать несколько одновременно.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500">Сетка:</span>
          <GridSelector value={cols} onChange={setCols} />
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-6 mb-8">
        <section className="glass p-6 md:col-span-2">
          <div
            className="rounded-2xl border-2 border-dashed border-indigo-300/70 p-10 text-center cursor-pointer"
            style={{
              background:
                'linear-gradient(180deg, rgba(99,102,241,0.05), rgba(168,85,247,0.05))',
            }}
            onClick={() => fileRef.current?.click()}
          >
            <input ref={fileRef} type="file" accept="video/*" className="hidden" />
            <div
              className="mx-auto w-14 h-14 rounded-xl flex items-center justify-center text-white"
              style={{ background: 'linear-gradient(135deg,#4f46e5,#7c3aed)' }}
            >
              <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            </div>
            <h3 className="mt-4 font-bold text-lg">Перетащите видео сюда</h3>
            <p className="text-sm text-slate-600">.mp4 · .mov · .mkv · до 2 ГБ</p>
            <button className="btn btn-primary mt-5">Выбрать файл</button>
          </div>
        </section>

        <aside className="glass p-6">
          <h3 className="font-bold mb-4">Параметры обработки</h3>
          <div className="space-y-4">
            <div>
              <div className="label mb-1">Трекер</div>
              <select className="field" value={tracker} onChange={(e) => setTracker(e.target.value)}>
                {TRACKERS.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <div className="label mb-1">Модель эмоций</div>
              <select className="field" value={model} onChange={(e) => setModel(e.target.value)}>
                {MODELS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <div className="label mb-1">Устройство вычислений</div>
              <div className="seg w-full">
                <button
                  className={`flex-1 ${device === 'cpu' ? 'active' : ''}`}
                  onClick={() => setDevice('cpu')}
                >
                  CPU
                </button>
                <button
                  className={`flex-1 ${device === 'cuda' ? 'active' : ''}`}
                  onClick={() => setDevice('cuda')}
                >
                  GPU
                </button>
              </div>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium">Идентифицировать лица</div>
                <div className="text-xs text-slate-500">Сопоставлять с базой</div>
              </div>
              <Toggle defaultOn />
            </div>
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium">Сохранить аннотированное видео</div>
                <div className="text-xs text-slate-500">Экспорт .mp4 по завершении</div>
              </div>
              <Toggle />
            </div>
            <button className="btn btn-primary w-full mt-2">Запустить обработку</button>
          </div>
        </aside>
      </div>

      <h2 className="font-bold text-lg mb-4">Обрабатываемые видео</h2>
      {uploads.length === 0 ? (
        <div className="glass p-10 text-center text-slate-500">
          Пока ничего не загружено. Перетащите файл выше.
        </div>
      ) : (
        <StreamGrid sources={uploads} cols={cols} detailPathPrefix="/uploads" />
      )}

      <p className="text-xs text-slate-500 mt-6 text-center">
        Нажмите на любой тайл, чтобы развернуть его на весь экран и управлять воспроизведением.
      </p>
    </>
  );
}
