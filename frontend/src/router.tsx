import { Navigate, createBrowserRouter, useParams } from 'react-router-dom';
import Layout from './components/Layout';
import HomePage from './pages/HomePage';
import SessionPage from './pages/SessionPage';
import RecordingsPage from './pages/RecordingsPage';
import HistoryPage from './pages/HistoryPage';
import UserHistoryPage from './pages/UserHistoryPage';
import SettingsPage from './pages/SettingsPage';
import AboutPage from './pages/AboutPage';

function LegacySession() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/session/${id}`} replace />;
}

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'session/:id', element: <SessionPage /> },
      { path: 'recordings', element: <RecordingsPage /> },
      { path: 'recordings/:id', element: <RecordingsPage /> },
      { path: 'history', element: <HistoryPage /> },
      { path: 'history/:userId', element: <UserHistoryPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'about', element: <AboutPage /> },
      { path: 'cameras', element: <Navigate to="/" replace /> },
      { path: 'uploads', element: <Navigate to="/" replace /> },
      { path: 'cameras/:id', element: <LegacySession /> },
      { path: 'uploads/:id', element: <LegacySession /> },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);
