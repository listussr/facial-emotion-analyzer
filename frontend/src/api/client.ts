/**
 * Тонкий клиент к FastAPI бэкенду. Все запросы идут через Vite-прокси (/api → :8000).
 */
import {
  CameraSource,
  UploadSource,
  StreamSource,
  TrackerName,
  EmotionModel,
  Device,
  UserSummary,
  UserHistoryDetail,
  SessionHistoryDetail,
  SearchMatch,
} from '@/types';

interface SessionConfigDTO {
  tracker: TrackerName;
  model: EmotionModel;
  device: Device;
}

interface SessionInfoDTO {
  id: string;
  kind: 'camera' | 'upload';
  name: string;
  source: string;
  status: string;
  config: SessionConfigDTO;
  frame_rate: number;
  started_at: string;
  frames_sent?: number;
  errors?: number;
  fps?: number;
  progress?: number;
  position_sec?: number;
  filename?: string | null;
  duration_sec?: number | null;
  file_size?: number | null;
}

function dtoToSource(dto: SessionInfoDTO): StreamSource {
  const base = {
    id: dto.id,
    name: dto.name,
    tracker: dto.config.tracker,
    model: dto.config.model,
    device: dto.config.device,
    fps: dto.fps ?? 0,
    latencyMs: 0,
    resolution: '—',
    faces: [],
    framesSent: dto.frames_sent ?? 0,
    errors: dto.errors ?? 0,
  };
  if (dto.kind === 'camera') {
    return {
      ...base,
      kind: 'camera',
      url: dto.source,
      status:
        dto.status === 'live'
          ? 'live'
          : dto.status === 'error'
          ? 'error'
          : dto.status === 'offline'
          ? 'offline'
          : 'slow',
    } as CameraSource;
  }
  return {
    ...base,
    kind: 'upload',
    filename: dto.filename || dto.name,
    durationSec: dto.duration_sec || 0,
    positionSec: dto.position_sec || 0,
    status:
      dto.status === 'running'
        ? 'running'
        : dto.status === 'done'
        ? 'done'
        : dto.status === 'paused'
        ? 'paused'
        : 'queued',
    progress: dto.progress || 0,
  } as UploadSource;
}

async function jsonFetch<T>(url: string, init?: RequestInit, timeoutMs = 8000): Promise<T> {
  const ctrl = new AbortController();
  const t = window.setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(url, {
      ...init,
      signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    });
    if (!r.ok) {
      const text = await r.text().catch(() => '');
      throw new Error(`${r.status} ${r.statusText}: ${text}`);
    }
    return (await r.json()) as T;
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      throw new Error(`Превышено время ожидания запроса (${timeoutMs} мс)`);
    }
    throw e;
  } finally {
    window.clearTimeout(t);
  }
}

export const api = {
  async listSessions(kind?: 'camera' | 'upload'): Promise<StreamSource[]> {
    const q = kind ? `?kind=${kind}` : '';
    const list = await jsonFetch<SessionInfoDTO[]>(`/api/sessions${q}`);
    return list.map(dtoToSource);
  },

  async createCamera(payload: {
    name: string;
    source: string;
    frame_rate: number;
    config: SessionConfigDTO;
  }): Promise<StreamSource> {
    const dto = await jsonFetch<SessionInfoDTO>('/api/sessions/camera', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return dtoToSource(dto);
  },

  async stopSession(id: string): Promise<void> {
    const r = await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`Failed to stop ${id}`);
  },

  async uploadFile(file: File): Promise<{
    upload_id: string;
    filename: string;
    size: number;
    uploaded_at: number | null;
  }> {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/uploads', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },

  async listUploads(): Promise<
    {
      upload_id: string;
      filename: string;
      size: number;
      saved_path: string;
      uploaded_at: number | null;
    }[]
  > {
    return jsonFetch('/api/uploads');
  },

  async deleteUpload(uploadId: string): Promise<void> {
    const r = await fetch(`/api/uploads/${uploadId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
  },

  async startUploadSession(payload: {
    upload_id: string;
    name: string;
    frame_rate: number;
    config: SessionConfigDTO;
  }): Promise<StreamSource> {
    const dto = await jsonFetch<SessionInfoDTO>('/api/uploads/session', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return dtoToSource(dto);
  },

  streamUrl(id: string): string {
    return `/api/stream/${id}`;
  },

  eventsWsUrl(id: string): string {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/api/events/${id}`;
  },

  // ---------- history ----------

  async listUsers(): Promise<UserSummary[]> {
    const data = await jsonFetch<{ users: UserSummary[] }>('/api/history/users');
    return data.users;
  },

  async searchByFace(file: File, topK: number): Promise<SearchMatch[]> {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`/api/history/search?top_k=${topK}`, {
      method: 'POST',
      body: fd,
    });
    if (!r.ok) throw new Error(await r.text());
    const data = (await r.json()) as { matches: SearchMatch[] };
    return data.matches;
  },

  async userHistory(userId: string): Promise<UserHistoryDetail> {
    return jsonFetch<UserHistoryDetail>(`/api/history/users/${userId}`);
  },

  userFaceUrl(userId: string): string {
    return `/api/history/users/${userId}/face`;
  },

  async sessionHistory(sessionId: string): Promise<SessionHistoryDetail> {
    return jsonFetch<SessionHistoryDetail>(`/api/history/sessions/${sessionId}`);
  },
};
