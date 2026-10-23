import { ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import StreamTile, { formatTime } from '@/components/StreamTile';
import { api, UploadEntry } from '@/api/client';
import { browserCapture } from '@/capture/browserCapture';
import { useBrowserCapture } from '@/capture/useBrowserCapture';
import { isRecommended, toSessionConfig, useAnalysisDefaults } from '@/hooks/useAnalysisDefaults';
import { useSessions } from '@/hooks/useSessions';
import { isActive, statusText } from '@/data/sessionStatus';
import type { Modality } from '@/types';

type SourceKind = 'browser' | 'file' | 'stream';

const SOURCES: { value: SourceKind; title: string; text: string; icon: JSX.Element }[] = [
  { value: 'browser', title: 'Камера и микрофон', text: 'Этого компьютера, прямо из браузера', icon: <CameraIcon /> },
  { value: 'file', title: 'Видеофайл', text: 'mp4, mov, mkv, webm или аудио', icon: <FileIcon /> },
  { value: 'stream', title: 'IP-камера или поток', text: 'Ссылка rtsp://, http:// или номер камеры', icon: <LinkIcon /> },
];

const WHAT: { value: string; label: string; modalities: Modality[] }[] = [
  { value: 'both', label: 'Лицо и голос', modalities: ['video', 'audio'] },
  { value: 'video', label: 'Только лицо', modalities: ['video'] },
  { value: 'audio', label: 'Только голос', modalities: ['audio'] },
];

const ACCEPT = '.mp4,.mov,.mkv,.avi,.webm,.wav,.mp3,.m4a,.flac,.ogg,.aac,.opus';

function defaultName(kind: SourceKind): string {
  const time = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  return `${kind === 'browser' ? 'Камера' : 'Поток'} ${time}`;
}

export default function HomePage() {
  const navigate = useNavigate();
  const { defaults } = useAnalysisDefaults();
  const capture = useBrowserCapture();
  const [kind, setKind] = useState<SourceKind>('file');
  const [what, setWhat] = useState('both');
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [picked, setPicked] = useState<UploadEntry | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const modalities = WHAT.find((w) => w.value === what)!.modalities;
  const capturing = capture.status === 'starting' || capture.status === 'live';
  const ready =
    kind === 'browser' ? !capturing : kind === 'file' ? Boolean(file || picked) : url.trim().length > 0;

  async function start() {
    setError(null);
    const config = toSessionConfig(defaults, modalities);
    try {
      let id: string;
      if (kind === 'browser') {
        setBusy('Ждём разрешения на камеру…');
        id = await browserCapture.start({
          name: name.trim() || defaultName('browser'),
          frameRate: Math.min(defaults.frameRate, 30),
          config,
        });
      } else if (kind === 'stream') {
        setBusy('Подключаемся к источнику…');
        const s = await api.createCamera({
          name: name.trim() || defaultName('stream'),
          source: url.trim(),
          frame_rate: defaults.frameRate,
          config,
        });
        id = s.id;
      } else {
        let target = picked?.upload_id;
        let filename = picked?.filename ?? '';
        if (file) {
          setBusy('Загружаем файл…');
          const up = await api.uploadFile(file);
          target = up.upload_id;
          filename = file.name;
        }
        setBusy('Запускаем анализ…');
        const s = await api.startUploadSession({
          upload_id: target!,
          name: name.trim() || filename || 'Видео',
          frame_rate: defaults.frameRate,
          config,
        });
        id = s.id;
      }
      navigate(`/session/${id}`);
    } catch (e: any) {
      setError(e?.message || 'Не удалось начать анализ');
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="mb-6">
        <h1 className="text-3xl font-extrabold tracking-tight">Новый анализ</h1>
        <p className="text-slate-600 mt-1">
          Система найдёт лица, распознает эмоции по лицу и голосу и сохранит итоговую запись.
        </p>
      </div>

      <section className="glass p-6 mb-8">
        <Step n={1} title="Откуда взять видео">
          <div className="grid sm:grid-cols-3 gap-3">
            {SOURCES.map((s) => (
              <button
                key={s.value}
                type="button"
                className={`choice ${kind === s.value ? 'active' : ''}`}
                onClick={() => {
                  setKind(s.value);
                  setError(null);
                }}
              >
                <span className="text-indigo-600 shrink-0">{s.icon}</span>
                <span>
                  <span className="block font-semibold">{s.title}</span>
                  <span className="block hint">{s.text}</span>
                </span>
              </button>
            ))}
          </div>

          <div className="mt-4">
            {kind === 'file' && (
              <FilePicker
                file={file}
                picked={picked}
                onFile={(f) => {
                  setFile(f);
                  setPicked(null);
                }}
                onPick={(u) => {
                  setPicked(u);
                  setFile(null);
                }}
                onError={setError}
              />
            )}
            {kind === 'stream' && (
              <input
                className="field"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="rtsp://user:pass@192.168.0.10:554/stream"
              />
            )}
            {kind === 'browser' && (
              <p className="hint">
                {capturing
                  ? 'Камера браузера уже используется — остановите текущий захват в окне справа внизу.'
                  : 'Браузер спросит разрешение на камеру и микрофон. Анализ идёт, пока открыта эта вкладка.'}
              </p>
            )}
          </div>
        </Step>

        <Step n={2} title="Что анализировать">
          <div className="seg">
            {WHAT.map((w) => (
              <button key={w.value} type="button" className={what === w.value ? 'active' : ''} onClick={() => setWhat(w.value)}>
                {w.label}
              </button>
            ))}
          </div>
        </Step>

        <Step n={3} title="Название" optional>
          <input
            className="field max-w-md"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={kind === 'file' ? 'По имени файла' : defaultName(kind)}
            maxLength={80}
          />
        </Step>

        {error && <div className="mb-4 rounded-xl bg-rose-50 border border-rose-200 p-3 text-sm text-rose-700">{error}</div>}

        <div className="flex items-center gap-4 flex-wrap">
          <button type="button" className="btn btn-primary btn-lg" disabled={!ready || busy !== null} onClick={start}>
            <PlayIcon />
            {busy ?? 'Начать анализ'}
          </button>
          <span className="hint">
            Параметры: {isRecommended(defaults) ? 'рекомендуемые' : 'свои'} ·{' '}
            <Link to="/settings" className="text-indigo-700 font-medium">
              изменить
            </Link>
          </span>
        </div>
      </section>

      <ActiveSessions />
    </>
  );
}

function Step({ n, title, optional, children }: { n: number; title: string; optional?: boolean; children: ReactNode }) {
  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-3">
        <span className="step-num">{n}</span>
        <h2 className="font-semibold">{title}</h2>
        {optional && <span className="hint">необязательно</span>}
      </div>
      {children}
    </div>
  );
}

function FilePicker({
  file,
  picked,
  onFile,
  onPick,
  onError,
}: {
  file: File | null;
  picked: UploadEntry | null;
  onFile: (f: File) => void;
  onPick: (u: UploadEntry) => void;
  onError: (e: string | null) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<UploadEntry[]>([]);
  const [showAll, setShowAll] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setUploads(await api.listUploads());
    } catch {
      setUploads([]);
    }
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  async function remove(u: UploadEntry) {
    if (!window.confirm(`Удалить файл «${u.filename}» с сервера?`)) return;
    try {
      await api.deleteUpload(u.upload_id);
      onError(null);
      await refresh();
    } catch (e: any) {
      onError(e?.message || 'Не удалось удалить файл');
    }
  }

  const shown = showAll ? uploads : uploads.slice(0, 4);
  return (
    <div className="space-y-4">
      <div
        className="rounded-2xl border-2 border-dashed border-slate-300 hover:border-indigo-300 p-6 text-center cursor-pointer transition-colors"
        onClick={() => ref.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const f = e.dataTransfer.files?.[0];
          if (f) onFile(f);
        }}
      >
        <input
          ref={ref}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
        />
        {file ? (
          <div>
            <div className="font-semibold">{file.name}</div>
            <div className="hint">{(file.size / 1e6).toFixed(1)} МБ · нажмите, чтобы выбрать другой</div>
          </div>
        ) : (
          <div>
            <div className="font-semibold">Перетащите файл сюда или нажмите, чтобы выбрать</div>
            <div className="hint">видео или аудио, до 2 ГБ</div>
          </div>
        )}
      </div>

      {uploads.length > 0 && (
        <div>
          <div className="label mb-2">Или выберите загруженный ранее</div>
          <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200">
            {shown.map((u) => (
              <li key={u.upload_id} className="flex items-center gap-3 px-3 py-2">
                <label className="flex items-center gap-3 flex-1 min-w-0 cursor-pointer">
                  <input
                    type="radio"
                    name="upload"
                    checked={picked?.upload_id === u.upload_id}
                    onChange={() => onPick(u)}
                  />
                  <span className="truncate">{u.filename}</span>
                  <span className="hint shrink-0">
                    {u.duration_sec ? formatTime(u.duration_sec) : ''}
                    {u.has_audio === false ? ' · без звука' : ''}
                    {u.has_video === false ? ' · только звук' : ''}
                  </span>
                </label>
                <button type="button" className="text-xs text-slate-400 hover:text-rose-600" onClick={() => remove(u)}>
                  удалить
                </button>
              </li>
            ))}
          </ul>
          {uploads.length > 4 && (
            <button type="button" className="text-sm text-indigo-700 mt-2" onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'Свернуть' : `Показать все (${uploads.length})`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ActiveSessions() {
  const { sessions, stop } = useSessions(undefined, 3000);
  if (sessions.length === 0) return null;
  const active = sessions.filter(isActive);
  const finished = sessions.filter((s) => !isActive(s));

  return (
    <>
      {active.length > 0 && (
        <section className="mb-8">
          <h2 className="text-xl font-bold mb-3">Идёт сейчас</h2>
          <div className="grid md:grid-cols-2 gap-4">
            {active.map((s) => (
              <div key={s.id} className="glass p-3">
                <Link to={`/session/${s.id}`} className="block">
                  <StreamTile source={s} live hideLabels />
                </Link>
                <div className="flex items-center justify-between gap-3 mt-3 px-1">
                  <div className="min-w-0">
                    <div className="font-semibold truncate">{s.name}</div>
                    <div className="hint">{statusText(s)}</div>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <Link to={`/session/${s.id}`} className="btn btn-ghost text-sm py-1.5">
                      Открыть
                    </Link>
                    <button type="button" className="btn btn-ghost text-sm py-1.5" onClick={() => stop(s.id)}>
                      Остановить
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {finished.length > 0 && (
        <section>
          <h2 className="text-xl font-bold mb-3">Завершены</h2>
          <ul className="glass divide-y divide-slate-100">
            {finished.map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <div className="font-medium truncate">{s.name}</div>
                  <div className="hint">{statusText(s)}</div>
                </div>
                <div className="flex gap-2 shrink-0">
                  <Link to={`/session/${s.id}`} className="btn btn-ghost text-sm py-1.5">
                    Итоги
                  </Link>
                  <Link to={`/recordings/${s.id}`} className="btn btn-ghost text-sm py-1.5">
                    Запись
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function CameraIcon() {
  return (
    <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M23 7l-7 5 7 5V7z" />
      <rect x="1" y="5" width="15" height="14" rx="2" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="currentColor">
      <polygon points="6 4 20 12 6 20 6 4" />
    </svg>
  );
}
