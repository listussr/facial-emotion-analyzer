import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { browserCapture } from '@/capture/browserCapture';
import { useBrowserCapture } from '@/capture/useBrowserCapture';

export default function BrowserCaptureBar() {
  const s = useBrowserCapture();
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (videoRef.current) videoRef.current.srcObject = s.stream;
  }, [s.stream]);

  if (s.status === 'idle') return null;

  return (
    <div className="fixed bottom-4 right-4 z-40 glass p-3 w-72 shadow-xl">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="pulse-dot"
            style={{ background: s.status === 'live' ? '#ef4444' : s.status === 'error' ? '#f59e0b' : '#a78bfa' }}
          />
          <span className="font-semibold text-sm truncate">
            {s.status === 'live' ? 'Камера браузера' : s.status === 'starting' ? 'Подключение…' : 'Захват остановлен'}
          </span>
        </div>
        {s.status === 'error' ? (
          <button type="button" className="btn btn-ghost text-xs py-1 px-2" onClick={() => browserCapture.stop()}>
            Скрыть
          </button>
        ) : (
          <button type="button" className="btn btn-primary text-xs py-1 px-2" onClick={() => browserCapture.stop()}>
            Остановить
          </button>
        )}
      </div>
      {s.stream && s.stream.getVideoTracks().length > 0 && (
        <video ref={videoRef} className="w-full rounded-lg bg-black mb-2" autoPlay muted playsInline />
      )}
      {s.error ? (
        <p className="text-xs text-rose-700">{s.error}</p>
      ) : (
        <div className="text-xs text-slate-600 flex flex-wrap gap-x-3">
          <span>{s.name}</span>
          <span className="font-mono">кадров: {s.framesSent}</span>
          {s.framesSkipped > 0 && <span className="font-mono">пропущено: {s.framesSkipped}</span>}
          {s.audioSeconds > 0 && <span className="font-mono">звук: {s.audioSeconds.toFixed(0)} с</span>}
        </div>
      )}
      {s.sessionId && s.status === 'live' && (
        <Link to={`/session/${s.sessionId}`} className="text-xs text-indigo-700 font-medium mt-1 inline-block">
          Открыть анализ →
        </Link>
      )}
    </div>
  );
}
