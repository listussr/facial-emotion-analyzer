import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import StreamTile from '@/components/StreamTile';
import { useSessions } from '@/hooks/useSessions';
import { api } from '@/api/client';

export default function HomePage() {
  const cameras = useSessions('camera');
  const uploads = useSessions('upload');

  return (
    <>
      <section className="relative overflow-hidden glass p-10 mb-8">
        <div className="grid-bg" />
        <div className="relative">
          <div className="chip mb-4">
            <span className="pulse-dot" />
            НИР · 4 курс · 1 семестр
          </div>
          <h1 className="text-5xl md:text-6xl font-extrabold tracking-tight leading-[1.05]">
            Понимай аудиторию.
            <br />
            <span className="text-gradient">В реальном времени.</span>
          </h1>
          <p className="mt-5 text-lg text-slate-600 max-w-2xl">
            Affectra — это локальная система, которая обнаруживает, отслеживает,
            идентифицирует и считывает эмоции каждого лица в видеопотоке или загруженном файле.
            Подбирайте алгоритмы под своё железо, выбирайте удобную сетку отображения и
            наблюдайте, как оживают данные.
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link to="/cameras" className="btn btn-primary">
              <PlayIcon /> Камеры в реальном времени
            </Link>
            <Link to="/uploads" className="btn btn-ghost">
              <UploadIcon /> Обработать видеофайл
            </Link>
          </div>
        </div>
      </section>

      <section className="grid md:grid-cols-3 gap-5 mb-8">
        <StepCard num={1} title="Подключите" body="IP-камеры, веб-камера или RTSP. Каждая в своей сессии." link="/settings" linkText="Настроить →" />
        <StepCard num={2} title="Настройте" body="Выберите трекер, модель эмоций и устройство вычислений." link="/settings" linkText="Алгоритмы →" />
        <StepCard num={3} title="Наблюдайте" body="Сетка 1×1 / 2×2 / 3×3, либо разверните любой стрим на весь экран." link="/cameras" linkText="К камерам →" />
      </section>

      <section className="grid md:grid-cols-2 gap-6">
        <PreviewBlock
          title="Активные камеры"
          link="/cameras"
          linkText="Все камеры →"
          empty={cameras.sessions.length === 0}
        >
          {cameras.sessions.slice(0, 3).map((c) => (
            <Link key={c.id} to={`/cameras/${c.id}`} className="glass p-3 block hover:scale-[1.01] transition-transform">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-semibold text-sm">{c.name}</span>
                <span className="text-[11px] text-slate-500 font-mono">
                  {c.kind === 'camera' ? c.status : ''}
                </span>
              </div>
              <StreamTile source={c} liveSrc={api.streamUrl(c.id)} hideLabels />
            </Link>
          ))}
        </PreviewBlock>

        <PreviewBlock
          title="Обрабатываемые видео"
          link="/uploads"
          linkText="Все видео →"
          empty={uploads.sessions.length === 0}
        >
          {uploads.sessions.slice(0, 3).map((u) => (
            <Link key={u.id} to={`/uploads/${u.id}`} className="glass p-3 block hover:scale-[1.01] transition-transform">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-semibold text-sm truncate">
                  {u.kind === 'upload' ? u.filename : u.name}
                </span>
                <span className="text-[11px] text-slate-500 font-mono shrink-0">
                  {u.kind === 'upload' ? `${Math.round(u.progress * 100)}%` : ''}
                </span>
              </div>
              <StreamTile source={u} liveSrc={api.streamUrl(u.id)} hideLabels />
            </Link>
          ))}
        </PreviewBlock>
      </section>
    </>
  );
}

function StepCard({
  num,
  title,
  body,
  link,
  linkText,
}: {
  num: number;
  title: string;
  body: string;
  link: string;
  linkText: string;
}) {
  return (
    <div className="glass p-5">
      <div className="flex items-center gap-3 mb-2">
        <span className="step-num">{num}</span>
        <h3 className="font-bold">{title}</h3>
      </div>
      <p className="text-sm text-slate-600">{body}</p>
      <Link to={link} className="mt-3 inline-block text-sm text-indigo-700 font-medium">
        {linkText}
      </Link>
    </div>
  );
}

function PreviewBlock({
  title,
  link,
  linkText,
  empty,
  children,
}: {
  title: string;
  link: string;
  linkText: string;
  empty: boolean;
  children: ReactNode;
}) {
  return (
    <div className="glass p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-bold text-lg">{title}</h2>
        <Link to={link} className="btn btn-ghost text-sm">
          {linkText}
        </Link>
      </div>
      {empty ? (
        <p className="text-sm text-slate-500">Пусто. Добавьте источник в настройках.</p>
      ) : (
        <div className="grid sm:grid-cols-1 gap-3">{children}</div>
      )}
    </div>
  );
}

function PlayIcon() {
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  );
}
function UploadIcon() {
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}
