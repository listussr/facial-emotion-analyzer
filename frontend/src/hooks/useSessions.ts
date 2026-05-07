import { useEffect, useRef, useState, useCallback } from 'react';
import { api } from '@/api/client';
import type { StreamSource } from '@/types';

interface State {
  sessions: StreamSource[];
  loading: boolean;
  error: string | null;
}

/**
 * Поллинг списка сессий заданного типа. Возвращает данные + функции
 * для ручного обновления и остановки сессии.
 */
export function useSessions(kind: 'camera' | 'upload', intervalMs = 2500) {
  const [state, setState] = useState<State>({ sessions: [], loading: true, error: null });
  const stopped = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listSessions(kind);
      if (stopped.current) return;
      setState({ sessions: list, loading: false, error: null });
    } catch (e: any) {
      if (stopped.current) return;
      setState((s) => ({ ...s, loading: false, error: e?.message || 'fetch failed' }));
    }
  }, [kind]);

  useEffect(() => {
    stopped.current = false;
    refresh();
    const t = window.setInterval(refresh, intervalMs);
    return () => {
      stopped.current = true;
      window.clearInterval(t);
    };
  }, [refresh, intervalMs]);

  const stop = useCallback(
    async (id: string) => {
      await api.stopSession(id);
      await refresh();
    },
    [refresh]
  );

  return { ...state, refresh, stop };
}
