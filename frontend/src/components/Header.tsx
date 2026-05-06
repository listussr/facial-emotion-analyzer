import { NavLink, Link } from 'react-router-dom';

const NAV: { to: string; label: string }[] = [
  { to: '/', label: 'Главная' },
  { to: '/cameras', label: 'Камеры' },
  { to: '/uploads', label: 'Видео' },
  { to: '/settings', label: 'Настройки' },
  { to: '/about', label: 'О системе' },
];

export default function Header() {
  return (
    <header className="sticky top-0 z-30 backdrop-blur-md bg-white/55 border-b border-indigo-200/40">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-3">
          <div
            className="w-9 h-9 rounded-xl flex items-center justify-center text-white font-bold"
            style={{
              background: 'linear-gradient(135deg,#4f46e5,#7c3aed,#06b6d4)',
              boxShadow: '0 10px 24px -8px rgba(79,70,229,.55)',
            }}
          >
            Æ
          </div>
          <div>
            <div className="font-extrabold tracking-tight text-lg leading-none">Affectra</div>
            <div className="text-[11px] text-indigo-700/70 font-mono leading-tight">
              анализ эмоций по лицу
            </div>
          </div>
        </Link>

        <nav className="flex items-center gap-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `nav-pill ${isActive ? 'active' : ''}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="flex items-center gap-2">
          <span className="chip chip-ok">
            <span className="pulse-dot" style={{ background: '#10b981' }} />
            бэкенд активен
          </span>
          <span className="chip font-mono">26 FPS</span>
        </div>
      </div>
    </header>
  );
}
