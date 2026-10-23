import { useEffect, useState } from 'react';
import { NavLink, Link } from 'react-router-dom';

const NAV: { to: string; label: string; end?: boolean }[] = [
  { to: '/', label: 'Анализ', end: true },
  { to: '/recordings', label: 'Записи' },
  { to: '/history', label: 'Люди' },
  { to: '/settings', label: 'Настройки' },
];

type ServerState = { online: boolean; notReady: string[] };

// Связь с сервером и готовность сервисов анализа; пока модели загружаются, опрос чаще
function useServerState(intervalMs = 10000): ServerState | null {
  const [state, setState] = useState<ServerState | null>(null);
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const check = async () => {
      let next: ServerState = { online: false, notReady: [] };
      try {
        const r = await fetch('/api/health', { cache: 'no-store' });
        if (r.ok) {
          const body = await r.json();
          next = { online: true, notReady: Array.isArray(body.not_ready) ? body.not_ready : [] };
        }
      } catch {
        // нет связи
      }
      if (!alive) return;
      setState(next);
      timer = window.setTimeout(check, next.online && next.notReady.length === 0 ? intervalMs : 3000);
    };
    check();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [intervalMs]);
  return state;
}

export default function Header() {
  const server = useServerState();
  const status = !server ? null : !server.online ? 'offline' : server.notReady.length ? 'loading' : 'ok';
  const chip = {
    ok: { cls: 'chip-ok', color: '#10b981', text: 'сервер на связи', title: 'Связь с сервером анализа' },
    loading: {
      cls: 'chip-warn',
      color: '#f59e0b',
      text: 'модели загружаются…',
      title: ['Анализ пока недоступен:', ...(server?.notReady ?? [])].join('\n'),
    },
    offline: { cls: 'chip-warn', color: '#f59e0b', text: 'нет связи с сервером', title: 'Связь с сервером анализа' },
  };
  return (
    <header className="sticky top-0 z-30 bg-white/90 backdrop-blur border-b border-slate-200">
      <div className="max-w-6xl mx-auto px-3 sm:px-6 h-14 flex items-center gap-2 sm:gap-4">
        <Link to="/" className="flex items-center gap-2 shrink-0">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-bold">Æ</div>
          <span className="font-extrabold tracking-tight text-lg hidden sm:inline">Affectra</span>
        </Link>

        <nav className="flex items-center sm:gap-1 overflow-x-auto no-scrollbar">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `nav-pill whitespace-nowrap ${isActive ? 'active' : ''}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto shrink-0">
          {status && (
            <span className={`chip ${chip[status].cls}`} title={chip[status].title}>
              <span
                className={`w-2 h-2 rounded-full ${status === 'loading' ? 'animate-pulse' : ''}`}
                style={{ background: chip[status].color }}
              />
              <span className="hidden sm:inline">{chip[status].text}</span>
            </span>
          )}
        </div>
      </div>
    </header>
  );
}
