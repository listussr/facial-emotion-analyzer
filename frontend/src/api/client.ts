/**
 * Тонкий клиент к FastAPI бэкенду. Все запросы идут через Vite-прокси (/api → :8000).
 */
import {
  CameraSource,
  UploadSource,
  StreamSource,
  TrackerName,
  EmotionModel,
  VoiceModel,
  Device,
  Modality,
  HistoryModality,
  FusionMethod,
  ClipFrames,
  UserSummary,
  UserHistoryDetail,
  SessionHistoryDetail,
  SearchMatch,
  VoiceHistory,
  RecordingInfo,
} from '@/types';

/** Настройки обработки сессии — уходят в топик session-config до старта продюсера. */
export interface SessionConfigDTO {
  tracker: TrackerName;
  model: EmotionModel;
  device: Device;
  modalities: Modality[];
  voice_model: VoiceModel;
  identification: boolean;
  /** null / нет — по умолчанию видеоконсьюмера */
  clip_frames?: ClipFrames | null;
  /** null / нет — по умолчанию сервиса слияния (stack_hidden) */
  fusion_method?: FusionMethod | null;
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
  modalities?: Modality[];
  degraded?: boolean;
  audio_seconds_sent?: number;
}

export interface UploadEntry {
  upload_id: string;
  filename: string;
  size: number;
  saved_path: string;
  uploaded_at: number | null;
  has_video?: boolean | null;
  has_audio?: boolean | null;
  duration_sec?: number | null;
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
    requestedModalities: dto.config.modalities ?? ['video'],
    modalities: dto.modalities ?? [],
    degraded: Boolean(dto.degraded),
    voiceModel: dto.config.voice_model,
    audioSeconds: dto.audio_seconds_sent ?? 0,
    fusionMethod: dto.config.fusion_method ?? null,
    clipFrames: dto.config.clip_frames ?? null,
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

async function errorDetail(r: Response): Promise<string> {
  const text = await r.text().catch(() => '');
  try {
    const detail = JSON.parse(text)?.detail;
    if (typeof detail === 'string') return detail;
  } catch {
    /* не JSON */
  }
  return text || `${r.status} ${r.statusText}`;
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

function withModality(url: string, modality?: HistoryModality): string {
  return modality ? `${url}?modality=${modality}` : url;
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
    // Создание включает проверку источника и публикацию конфига — дольше обычного запроса
    const dto = await jsonFetch<SessionInfoDTO>(
      '/api/sessions/camera',
      { method: 'POST', body: JSON.stringify(payload) },
      20000
    );
    return dtoToSource(dto);
  },

  async createBrowserSession(payload: {
    name: string;
    frame_rate: number;
    config: SessionConfigDTO;
  }): Promise<StreamSource> {
    const dto = await jsonFetch<SessionInfoDTO>(
      '/api/sessions/browser',
      { method: 'POST', body: JSON.stringify(payload) },
      20000
    );
    return dtoToSource(dto);
  },

  async stopSession(id: string): Promise<void> {
    const r = await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`Failed to stop ${id}`);
  },

  async stopAllSessions(kind?: 'camera' | 'upload'): Promise<{ stopped: string[]; count: number }> {
    const q = kind ? `?kind=${kind}` : '';
    const r = await fetch(`/api/sessions${q}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },

  async uploadFile(file: File): Promise<UploadEntry> {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/uploads', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await errorDetail(r));
    return r.json();
  },

  async listUploads(): Promise<UploadEntry[]> {
    return jsonFetch('/api/uploads');
  },

  async deleteUpload(uploadId: string): Promise<void> {
    const r = await fetch(`/api/uploads/${uploadId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await errorDetail(r));
  },

  async startUploadSession(payload: {
    upload_id: string;
    name: string;
    frame_rate: number;
    config: SessionConfigDTO;
  }): Promise<StreamSource> {
    const dto = await jsonFetch<SessionInfoDTO>(
      '/api/uploads/session',
      { method: 'POST', body: JSON.stringify(payload) },
      20000
    );
    return dtoToSource(dto);
  },

  streamUrl(id: string): string {
    return `/api/stream/${id}`;
  },

  /** Исходные кадры сессии (JPEG + pts) для отрисовки рамок в браузере */
  liveWsUrl(id: string): string {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/api/live/${id}`;
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

  /** История человека; modality: 'face' (по умолчанию) или 'fused' — после слияния с голосом. */
  async userHistory(userId: string, modality?: HistoryModality): Promise<UserHistoryDetail> {
    return jsonFetch<UserHistoryDetail>(withModality(`/api/history/users/${userId}`, modality));
  },

  userFaceUrl(userId: string): string {
    return `/api/history/users/${userId}/face`;
  },

  async sessionHistory(sessionId: string, modality?: HistoryModality): Promise<SessionHistoryDetail> {
    return jsonFetch<SessionHistoryDetail>(withModality(`/api/history/sessions/${sessionId}`, modality));
  },

  /** Голос сессии (окна с речью) — без привязки к человеку. */
  async sessionVoice(sessionId: string): Promise<VoiceHistory> {
    return jsonFetch<VoiceHistory>(`/api/history/sessions/${sessionId}/voice`);
  },

  /** CSV; без modality — все модальности (колонка modality в файле). */
  userExportCsvUrl(userId: string, modality?: HistoryModality): string {
    return withModality(`/api/history/users/${userId}/export.csv`, modality);
  },

  sessionExportCsvUrl(sessionId: string, modality?: HistoryModality): string {
    return withModality(`/api/history/sessions/${sessionId}/export.csv`, modality);
  },

  // ---------- recordings ----------

  async listRecordings(): Promise<RecordingInfo[]> {
    return jsonFetch<RecordingInfo[]>('/api/recordings');
  },

  async getRecording(sessionId: string): Promise<RecordingInfo | null> {
    const r = await fetch(`/api/recordings/${sessionId}`);
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(await errorDetail(r));
    return r.json();
  },

  async deleteRecording(sessionId: string): Promise<void> {
    const r = await fetch(`/api/recordings/${sessionId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await errorDetail(r));
  },

  recordingVideoUrl(sessionId: string, download = false): string {
    return `/api/recordings/${sessionId}/video${download ? '?download=true' : ''}`;
  },

  recordingAnalyticsUrl(sessionId: string): string {
    return `/api/recordings/${sessionId}/analytics?download=true`;
  },
};
