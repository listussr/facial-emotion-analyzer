export type TrackerName = 'deepsort' | 'bytetrack';
/**
 * Имя модели эмоций лица / голоса. Список доступных — в `data/models.ts`;
 * бэкенд заменяет несовместимую модель моделью по умолчанию.
 */
export type EmotionModel = string;
export type VoiceModel = string;
export type Device = 'cpu' | 'cuda';
export type Modality = 'video' | 'audio';
/** Модальность таймсерий в истории: эмоции лица или итог после слияния с голосом. */
export type HistoryModality = 'face' | 'fused';

/**
 * Текущий набор классов (CREMA-D, 6 эмоций) + классы старых записей
 * (8-классовые модели до перехода), которые ещё встречаются в истории.
 */
export type CurrentEmotion = 'anger' | 'disgust' | 'fear' | 'happy' | 'neutral' | 'sad';
export type LegacyEmotion = 'contempt' | 'surprise';
export type Emotion = CurrentEmotion | LegacyEmotion;

/** Порядок совпадает с src/emotion_labels.py — i-й элемент scores новых записей. */
export const CURRENT_EMOTIONS: CurrentEmotion[] = ['anger', 'disgust', 'fear', 'happy', 'neutral', 'sad'];
/** Порядок scores старых 8-классовых записей (без поля labels). */
export const LEGACY_LABELS: Emotion[] = [
  'anger', 'contempt', 'disgust', 'fear', 'happy', 'neutral', 'sad', 'surprise',
];

export const EMOTION_RU: Record<Emotion, string> = {
  anger: 'злость',
  contempt: 'презрение',
  disgust: 'отвращение',
  fear: 'страх',
  happy: 'радость',
  neutral: 'нейтрально',
  sad: 'грусть',
  surprise: 'удивление',
};

export const EMOTION_COLORS: Record<Emotion, string> = {
  anger: '#ef4444',
  contempt: '#a78bfa',
  disgust: '#10b981',
  fear: '#8b5cf6',
  happy: '#fbbf24',
  neutral: '#94a3b8',
  sad: '#3b82f6',
  surprise: '#f472b6',
};

export interface FaceBox {
  /** [x1, y1, x2, y2] в процентах от размера тайла (0-100) */
  bbox: [number, number, number, number];
  emotion: Emotion;
  score: number;
  faceId: string;
  trackId: number;
  color: string;
}

/** Алгоритм слияния лица и голоса (emotion_kit, models/crema/usage_rules.md, п. 4) */
export type FusionMethod =
  | 'stack_hidden'
  | 'stack_logits'
  | 'weighted_logits'
  | 'weighted_logits_robust'
  | 'confidence_weighted'
  | 'mean_probs'
  | 'audio_only'
  | 'video_only';

/** Кадров на окно модели эмоций лица */
export type ClipFrames = 8 | 16;

export interface SourceBase {
  id: string;
  name: string;
  tracker: TrackerName;
  model: EmotionModel;
  device: Device;
  fps: number;
  latencyMs: number;
  faces: FaceBox[];
  resolution: string;
  framesSent: number;
  errors: number;
  /** Запрошенные модальности сессии */
  requestedModalities?: Modality[];
  /** Что реально идёт (источник может не дать часть запрошенного) */
  modalities?: Modality[];
  degraded?: boolean;
  voiceModel?: VoiceModel;
  audioSeconds?: number;
  /** null — по умолчанию сервиса */
  fusionMethod?: FusionMethod | null;
  clipFrames?: ClipFrames | null;
}

export interface CameraSource extends SourceBase {
  kind: 'camera';
  url: string;
  status: 'live' | 'offline' | 'slow' | 'error';
}

export interface UploadSource extends SourceBase {
  kind: 'upload';
  filename: string;
  durationSec: number;
  positionSec: number;
  status: 'queued' | 'running' | 'paused' | 'done';
  progress: number; // 0..1
}

export type StreamSource = CameraSource | UploadSource;

// ---------- history ----------

export interface UserSummary {
  user_id: string;
  first_seen: string | null;
  last_seen: string | null;
  tracks_count: number;
  total_samples: number;
  dominant_emotion: string | null;
}

export interface SearchMatch extends UserSummary {
  similarity: number;
}

export interface TrackHistory {
  track_id: number | null;
  camera_id: string | null;
  user_id?: string | null;
  modality?: HistoryModality;
  label_set?: string;
  /** К каким классам относятся samples[].scores (у старых записей — 8 классов) */
  labels?: Emotion[];
  started_at: number | null;
  ended_at: number | null;
  samples: { t: number; label: Emotion; scores: number[] }[];
  created_at: string;
}

export interface UserHistoryDetail {
  user_id: string;
  modality?: HistoryModality;
  available_modalities?: HistoryModality[];
  total_samples: number;
  tracks_count: number;
  label_counts: Record<string, number>;
  tracks: TrackHistory[];
}

export interface SessionHistoryDetail {
  session_id: string;
  modality?: HistoryModality;
  available_modalities?: HistoryModality[];
  total_samples: number;
  tracks_count: number;
  unique_users: number;
  label_counts: Record<string, number>;
  tracks: TrackHistory[];
}

export interface VoiceHistory {
  session_id: string;
  label_set: string;
  labels: Emotion[];
  total_windows: number;
  label_counts: Record<string, number>;
  windows: {
    window_seq: number;
    pts_start_ms: number;
    pts_end_ms: number;
    t?: number;
    speech_prob?: number;
    label?: Emotion | null;
    smoothed_label?: Emotion | null;
    scores?: number[] | null;
  }[];
}

export type RecordingStatus = 'recording' | 'rendering' | 'ready' | 'failed';

/** Итоговая запись сессии в хранилище: видео с аннотациями и аналитика JSON. */
export interface RecordingInfo {
  session_id: string;
  name?: string | null;
  kind?: 'camera' | 'upload' | null;
  status: RecordingStatus;
  frames?: number | null;
  duration_sec?: number | null;
  size_bytes?: number | null;
  has_audio?: boolean | null;
  truncated: boolean;
  summary?: {
    fused_label_counts?: Record<string, number>;
    face_label_counts?: Record<string, number>;
    voice_label_counts?: Record<string, number>;
    tracks?: Record<string, { label_counts: Record<string, number>; dominant: Emotion }>;
  } | null;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}
