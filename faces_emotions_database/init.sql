CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE face_embeddings (
    id SERIAL PRIMARY KEY,
    user_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    embedding vector(512),
    first_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    face_image BYTEA NOT NULL
);

CREATE INDEX idx_face_embeddings ON face_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE emotion_timeseries (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES face_embeddings(user_id) ON DELETE CASCADE,
    data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_emotion_user_id ON emotion_timeseries(user_id);
