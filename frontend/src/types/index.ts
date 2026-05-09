export type TrackerName = 'deepsort' | 'bytetrack';
export type EmotionModel =
  | 'resnet-18'
  | 'resnet-18-int8'
  | 'resnet-50'
  | 'resnet-50-int8'
  | 'convnext'
  | 'convnext-int8';
export type Device = 'cpu' | 'cuda';

export type Emotion =
  | 'anger'
  | 'contempt'
  | 'disgust'
  | 'fear'
  | 'happy'
  | 'neutral'
  | 'sad'
  | 'surprise';

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
  started_at: number | null;
  ended_at: number | null;
  samples: { t: number; label: Emotion; scores: number[] }[];
  created_at: string;
}

export interface UserHistoryDetail {
  user_id: string;
  total_samples: number;
  tracks_count: number;
  label_counts: Record<string, number>;
  tracks: TrackHistory[];
}

export interface SessionHistoryDetail {
  session_id: string;
  total_samples: number;
  tracks_count: number;
  unique_users: number;
  label_counts: Record<string, number>;
  tracks: TrackHistory[];
}
