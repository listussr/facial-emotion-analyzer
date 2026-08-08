import { useEffect, useRef, useState, useCallback } from 'react';
import StreamGrid from '@/components/StreamGrid';
import GridSelector, { GridCols } from '@/components/GridSelector';
import Toggle from '@/components/Toggle';
import { useSessions } from '@/hooks/useSessions';
import { api } from '@/api/client';
import { EMOTION_MODELS } from '@/data/models';
import type { TrackerName, EmotionModel, Device } from '@/types';

interface UploadEntry {
  upload_id: string;
  filename: string;
  size: number;
  saved_path: string;
  uploaded_at: number | null;
}

const TRACKERS: { value: TrackerName; label: string }[] = [
  { value: 'deepsort', label: 'DeepSORT' },
  { value: 'bytetrack', label: 'ByteTrack' },
];
const MODELS = EMOTION_MODELS;

export default function UploadsPage() {
  const [cols, setCols] = useState<GridCols>(2);
  const [tracker, setTracker] = useState<TrackerName>('bytetrack');
  const [model, setModel] = useState<EmotionModel>('resnet-18');
  const [device, setDevice] = useState<Device>('cpu');
  const [frameRate, setFrameRate] = useState<number>(20);
  const [frameRateError, setFrameRateError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const FPS_MIN = 1;
  const FPS_MAX = 120;

  const { sessions, error: listError, stop, refresh } = useSessions('upload');

  // Список ранее загруженных файлов — чтобы можно было запустить тот же видеофайл
  // повторно без повторной загрузки.
  const [uploads, setUploads] = useState<UploadEntry[]>([]);
  const refreshUploads = useCallback(async () => {
    try {
      const list = await api.listUploads();
      setUploads(list);
    } catch {
      /* молча — раздел не критичен */
    }
  }, []);
  useEffect(() => {
    refreshUploads();
    const t = window.setInterval(refreshUploads, 5000);
    return () => window.clearInterval(t);
  }, [refreshUploads]);

  async function rerunUpload(uploadId: string, filename: string) {
    if (frameRateError) {
      setError(frameRateError);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.startUploadSession({
        upload_id: uploadId,
        name: filename,
        frame_rate: frameRate,
        config: { tracker, model, device },
      });
      await refresh();
    } catch (e: any) {
      setError(e?.message || 'Не удалось запустить обработку');
    } finally {
      setBusy(false);
    }
  }

  async function deleteUpload(uploadId: string) {
    try {
      await api.deleteUpload(uploadId);
      await refreshUploads();
    } catch (e: any) {
      setError(e?.message || 'Не удалось удалить файл');
    }
  }

  async function startProcessing() {
    if (!pendingFile) {
      fileRef.current?.click();
      return;
    }
    if (frameRateError) {
      setError(frameRateError);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const upload = await api.uploadFile(pendingFile);
      await api.startUploadSession({
        upload_id: upload.upload_id,
        name: pendingFile.name,
        frame_rate: frameRate,
        config: { tracker, model, device },
      });
      setPendingFile(null);
      if (fileRef.current) fileRef.current.value = '';
      await refresh();
      await refreshUploads();
    } catch (e: any) {
      setError(e?.message || 'Ошибка загрузки');
    } finally {
      setBusy(false);
    }
  }

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
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const f = e.dataTransfer.files?.[0];
              if (f) setPendingFile(f);
            }}
          >
            <input
              ref={fileRef}
              type="file"
              accept="video/*"
              className="hidden"
              onChange={(e) => setPendingFile(e.target.files?.[0] || null)}
            />
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
            <h3 className="mt-4 font-bold text-lg">
              {pendingFile ? pendingFile.name : 'Перетащите видео сюда'}
            </h3>
            <p className="text-sm text-slate-600">
              {pendingFile
                ? `Размер: ${(pendingFile.size / (1024 * 1024)).toFixed(1)} МБ`
                : '.mp4 · .mov · .mkv · до 2 ГБ'}
            </p>
            {!pendingFile && <button className="btn btn-primary mt-5">Выбрать файл</button>}
          </div>

          {error && <div className="text-sm text-rose-700 mt-3">{error}</div>}
        </section>

        <aside className="glass p-6">
          <h3 className="font-bold mb-4">Параметры обработки</h3>
          <div className="space-y-4">
            <div>
              <div className="label mb-1">Трекер</div>
              <select
                className="field"
                value={tracker}
                onChange={(e) => setTracker(e.target.value as TrackerName)}
              >
                {TRACKERS.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <div className="label mb-1">Модель эмоций</div>
              <select
                className="field"
                value={model}
                onChange={(e) => setModel(e.target.value as EmotionModel)}
              >
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
            <div>
              <div className="label mb-1">
                Частота кадров{' '}
                <span className="text-slate-400">(больше = быстрее обработка)</span>
              </div>
              <input
                className="field"
                type="number"
                value={frameRate}
                onChange={(e) => {
                  const val = Number(e.target.value);
                  setFrameRate(val);
                  if (!Number.isFinite(val) || val < FPS_MIN || val > FPS_MAX) {
                    setFrameRateError(
                      `Допустимый диапазон: ${FPS_MIN}–${FPS_MAX} кадров/сек`
                    );
                  } else {
                    setFrameRateError(null);
                  }
                }}
              />
              {frameRateError && (
                <div className="text-xs text-rose-700 mt-1">{frameRateError}</div>
              )}
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
            <button
              className="btn btn-primary w-full mt-2 disabled:opacity-50"
              onClick={startProcessing}
              disabled={busy}
            >
              {busy ? 'Загрузка…' : pendingFile ? 'Запустить обработку' : 'Выбрать файл'}
            </button>
          </div>
        </aside>
      </div>

      {uploads.length > 0 && (
        <section className="mb-8">
          <h2 className="font-bold text-lg mb-3">Ранее загруженные</h2>
          <p className="text-sm text-slate-500 mb-3">
            Запустите обработку повторно с текущими параметрами без новой загрузки.
          </p>
          <div className="glass divide-y divide-indigo-100/60">
            {uploads.map((u) => (
              <div
                key={u.upload_id}
                className="flex items-center justify-between gap-3 p-3 flex-wrap"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{u.filename}</div>
                  <div className="text-xs text-slate-500 font-mono">
                    {(u.size / (1024 * 1024)).toFixed(1)} МБ
                    {u.uploaded_at &&
                      ` · ${new Date(u.uploaded_at * 1000).toLocaleString('ru-RU')}`}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="btn btn-primary text-sm disabled:opacity-50"
                    onClick={() => rerunUpload(u.upload_id, u.filename)}
                    disabled={busy}
                  >
                    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                      <polygon points="5 3 19 12 5 21 5 3" />
                    </svg>
                    Запустить
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost text-sm"
                    onClick={() => {
                      if (confirm(`Удалить файл «${u.filename}»?`)) {
                        deleteUpload(u.upload_id);
                      }
                    }}
                    title="Удалить файл"
                  >
                    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                      <polyline points="3 6 5 6 21 6" />
                      <path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6" />
                    </svg>
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <h2 className="font-bold text-lg mb-4">Обрабатываемые видео</h2>
      {listError && (
        <div className="glass p-4 mb-4 text-sm text-rose-700">{listError}</div>
      )}
      {sessions.length === 0 ? (
        <div className="glass p-10 text-center text-slate-500">
          Пока ничего не загружено. Перетащите файл выше.
        </div>
      ) : (
        <StreamGrid
          sources={sessions}
          cols={cols}
          detailPathPrefix="/uploads"
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
