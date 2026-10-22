CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE face_embeddings (
    id SERIAL PRIMARY KEY,
    user_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    embedding vector(512),
    first_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    face_image BYTEA NOT NULL
);

CREATE INDEX idx_face_embeddings ON face_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Таймсерии эмоций трека. modality: 'face' (лицо) | 'fused' (после слияния с голосом).
-- label_set — набор классов scores ('crema-d-6'; 'legacy-8' — записи старых моделей).
-- Старые БД догоняются до этой схемы миграциями из src/db_managing/schema.py.
CREATE TABLE emotion_timeseries (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES face_embeddings(user_id) ON DELETE CASCADE,
    modality TEXT NOT NULL DEFAULT 'face',
    label_set TEXT NOT NULL DEFAULT 'legacy-8',
    data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_emotion_user_id ON emotion_timeseries(user_id);
CREATE INDEX idx_emotion_user_modality ON emotion_timeseries(user_id, modality);
CREATE INDEX idx_emotion_camera ON emotion_timeseries((data->>'camera_id'));

-- Голос сессии без привязки к человеку (окна аудио с речью).
CREATE TABLE session_audio_timeseries (
    id SERIAL PRIMARY KEY,
    camera_id TEXT NOT NULL,
    label_set TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_session_audio_camera ON session_audio_timeseries(camera_id);
