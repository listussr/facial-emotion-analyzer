import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/api/client';
import { RecordingInfo } from '@/types';
import { RECORDING_STATUS_LABEL } from '@/pages/RecordingsPage';

export default function RecordingChip({ sessionId }: { sessionId: string }) {
  const [rec, setRec] = useState<RecordingInfo | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const r = await api.getRecording(sessionId);
        if (!alive) return;
        setRec(r);
        if (!r || r.status === 'recording' || r.status === 'rendering') timer = window.setTimeout(poll, 3000);
      } catch {
        if (alive) timer = window.setTimeout(poll, 10000);
      }
    };
    poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [sessionId]);

  if (!rec) return null;
  const ready = rec.status === 'ready';
  return (
    <Link
      to={`/recordings/${sessionId}`}
      className={`chip ${ready ? 'chip-ok' : rec.status === 'failed' ? 'chip-warn' : 'chip-on'}`}
      title="Итоговая запись сессии в хранилище"
    >
      запись: {RECORDING_STATUS_LABEL[rec.status]}
      {ready ? ' →' : ''}
    </Link>
  );
}
