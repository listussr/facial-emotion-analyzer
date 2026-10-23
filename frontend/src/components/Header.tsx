import { useEffect, useState } from 'react';
import { NavLink, Link } from 'react-router-dom';

const NAV: { to: string; label: string; end?: boolean }[] = [
  { to: '/', label: 'Анализ', end: true },
  { to: '/recordings', label: 'Записи' },
  { to: '/history', label: 'Люди' },
  { to: '/settings', label: 'Настройки' },
];

function useServerOnline(intervalMs = 10000): boolean | null {
  const [online, setOnline] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const r = await fetch('/api/health', { cache: 'no-store' });
        if (alive) setOnline(r.ok);
      } catch {
        if (alive) setOnline(false);
      }
    };
    check();
    const t = window.setInterval(check, intervalMs);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [intervalMs]);
  return online;
}

export default function Header() {
  const online = useServerOnline();
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
          {online !== null && (
            <span className={`chip ${online ? 'chip-ok' : 'chip-warn'}`} title="Связь с сервером анализа">
              <span className="w-2 h-2 rounded-full" style={{ background: online ? '#10b981' : '#f59e0b' }} />
              <span className="hidden sm:inline">{online ? 'сервер на связи' : 'нет связи с сервером'}</span>
            </span>
          )}
        </div>
      </div>
    </header>
  );
}
