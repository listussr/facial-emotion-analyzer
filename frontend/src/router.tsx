import { createBrowserRouter } from 'react-router-dom';
import Layout from './components/Layout';
import HomePage from './pages/HomePage';
import CamerasPage from './pages/CamerasPage';
import UploadsPage from './pages/UploadsPage';
import SettingsPage from './pages/SettingsPage';
import AboutPage from './pages/AboutPage';
import StreamFocusPage from './pages/StreamFocusPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'cameras', element: <CamerasPage /> },
      { path: 'cameras/:id', element: <StreamFocusPage kind="camera" /> },
      { path: 'uploads', element: <UploadsPage /> },
      { path: 'uploads/:id', element: <StreamFocusPage kind="upload" /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'about', element: <AboutPage /> },
    ],
  },
]);
