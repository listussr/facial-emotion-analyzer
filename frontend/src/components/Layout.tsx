import { Link, Outlet } from 'react-router-dom';
import Header from './Header';
import BrowserCaptureBar from './BrowserCaptureBar';

export default function Layout() {
  return (
    <>
      <Header />
      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        <Outlet />
      </main>
      <footer className="max-w-6xl mx-auto px-4 sm:px-6 pb-8 hint flex gap-4">
        <span>Affectra · НИР</span>
        <Link to="/about" className="hover:text-slate-700">
          О системе
        </Link>
      </footer>
      <BrowserCaptureBar />
    </>
  );
}
