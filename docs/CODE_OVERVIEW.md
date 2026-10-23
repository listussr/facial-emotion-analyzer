# Обзор кодовой базы Affectra

Карта кода для того, кто открывает репозиторий впервые: процессы, пакеты, путь данных по коду, форматы
сообщений, конфигурация и соглашения. Архитектура и решения — в [`ARCHITECTURE.md`](ARCHITECTURE.md),
модули подробно — в [`modules/`](modules/README.md).

---

## 1. Процессы

| Точка входа | Сервис в compose | Что запускает |
|---|---|---|
| `webapp.py` | `webapp` | FastAPI (`src/web_server`): сессии командами, загрузки, раздача в браузер |
| `ingest.py` | `ingest` | `IngestService` — продюсеры сессий, статусы, WebSocket камеры браузера |
| `consumer.py` | `consumer` | `ProcessingPipeline` — лица, трекинг, идентификация, эмоции лица |
| `audio_consumer.py` | `audio-consumer` | `AudioPipeline` — VAD и эмоции голоса |
| `fusion_consumer.py` | `fusion` | `FusionService` — слияние лица и голоса, запись слитых таймсерий |
| `visualizer.py` | `visualizer` | `VideoAnnotator` — серверная разметка кадров |
| `archiver.py` | `archiver` | `ArchiveService` — итоговая запись сессии в S3 |
| `producer.py` | — | продюсер из командной строки (без веб-сервера), для отладки |

Все процессы собираются в один образ `Dockerfile.python` и отличаются командой запуска. Фоновые сервисы
(audio-consumer, visualizer, archiver) понижают свой приоритет, чтобы видеопайплайн получал CPU первым.

## 2. Пакет `src/`

```
src/
  ingest/                 сервис приёма: команды, продюсеры, статусы сессий, WS браузера
  video_producing/        источники медиа и продюсер в Kafka
    sources/              файл и поток (PyAV), вебкамера, микрофон, камера браузера
    producer.py           CameraProducer: темп, прореживание, отправка, события сессии
    contracts.py          VideoMessage, AudioMessage, SessionEvent (msgpack)
  frames_processing/      видеопайплайн
    processing/
      face_detection/     YuNet, MediaPipe, фильтры
      faces_tracking/     ByteTrack и DeepSORT за общим интерфейсом
      face_identification/ FaceNet и поиск в pgvector
      emotion_recognition/ модели эмоций по окну кадров, окна треков, сглаживание
    processing_pipline.py ProcessingPipeline: стадии A/B/C
    session_control.py    настройки видео по сессиям
    stream_metrics.py     отсев устаревших сообщений, задержки
    kafka_io.py           общий Kafka-ввод и вывод (используют и другие сервисы)
  audio_processing/       окна звука, VAD, модели голоса, сглаживание
  fusion/                 слияние, состояние голоса, запись таймсерий
  session_archive/        спул, рендер mp4 и JSON, клиент S3, сервис архивации
  session_config/         конфиги сессий в топике session-config
  service_health/         сигналы живости сервисов (service-health) и готовность к запуску сессий
  video_annotating/       отрисовка рамок, синхронизация кадров с аналитикой
  emotion_kit/            инференс моделей CREMA-D и методы слияния (перенесён из проекта обучения)
  db_managing/            схема и миграции БД, обёртки psycopg2
  web_server/             FastAPI: роуты, сервисы, схемы
  emotion_labels.py       единый набор из 6 классов
  cpu_threads.py          ограничение потоков OpenCV и приоритета процессов
  object_storage.py       клиент S3: загрузки и записи
  logging_config.py       JSON- или цветные логи
```

## 3. Путь данных по коду

### Кадр

1. `SessionManager._start` (`web_server/services/session_manager.py`) публикует конфиг сессии и команду `start`.
2. `IngestService.start` (`ingest/service.py`) готовит источник (скачивает загрузку из S3, проверяет поток) и
   запускает `CameraProducer`; `CameraProducer._run` открывает источник через `sources.open_source`. `FileSource` декодирует PyAV и
   уменьшает кадр прямо в декодере, `BrowserSource` берёт кадры из WebSocket.
3. `CameraProducer._loop` выпускает кадры в темпе реального времени (`RealtimePacer`), прореживает до
   `frame_rate` (`FrameDecimator`) и шлёт `VideoMessage` в `raw-video-frames`.
4. `ProcessingPipeline._stage_consume_decode` (поток A) читает сообщение и декодирует JPEG.
5. `ProcessingPipeline._handle_frame` (поток B): детекция раз в N кадров → `tracker.update` или
   `tracker.predict` → кроп, идентификация, кодирование кропа в фоне → `_update_track_emotion` раз в
   `emotion_hop_ms`.
6. `ProcessingPipeline._stage_produce` (поток C) пишет результат в `vision-analytics`.
7. `FusionService.handle_vision` дополняет лица голосом и пишет `fused-analytics`.
8. `EventsDispatcher` (webapp) раздаёт результат подписчикам `/api/events/{id}`; `ArchiveService`
   складывает его в спул.

### Аудиочанк

1. Источник выдаёт PCM; `AudioChunker` режет его на чанки по 100 мс с `pts_ms` на часах сессии.
2. `AudioPipeline.handle_chunk` склеивает чанки в `CameraAudioBuffer` и получает окна 3 с с шагом 1 с.
3. `EnergyVAD`, затем `SileroVAD` оценивают долю речи; окна с речью уходят в `VoiceEmotionRecognizer`.
4. Результат `AudioAnalytics` пишется в `audio-analytics`; `FusionService.handle_audio` добавляет его в
   `VoiceState` камеры.

## 4. Форматы сообщений

Все медиа и события — msgpack, аналитика — JSON. Ключ сообщения — `camera_id`.

### `raw-video-frames` (`VideoMessage`, schema 2)

| Поле | Тип | Смысл |
|---|---|---|
| `camera_id`, `seq`, `pts_ms` | str, int, int | сессия, номер кадра, время от начала сессии |
| `modalities` | list | что реально передаёт сессия |
| `capture_wall_ts`, `session_start_wall_ts` | float | время захвата и начала сессии (Unix) |
| `frame_data` | bytes | JPEG |
| `frame_rate`, `quality` | int | целевая частота и качество JPEG |
| `processed_width/height`, `original_width/height` | int | размеры после уменьшения и исходные |
| `frame_id`, `timestamp` | str, float | поля для аннотатора, сохранены для совместимости |

### `raw-audio-chunks` (`AudioMessage`)

Общие поля как у кадра, плюс `audio_data` (PCM s16le interleaved), `sample_rate` (16000), `channels` (1),
`num_samples` (на канал), `duration_ms`.

### `session-events` (`SessionEvent`)

`event` — `stream_start` или `stream_end`; `modalities` и `requested_modalities`; `degraded` — источник
не дал часть запрошенного; `is_live`; параметры потоков `video`/`audio`. У `stream_end` есть `reason`
(`eof`, `stopped`, `error`), `last_pts_ms` и `sent` по потокам.

### `session-config` (`SessionConfig`, schema 1)

```json
{
  "camera_id": "upload_1e3934", "revision": 1792755200000000000, "updated_at": 1792755200.0,
  "vision": {"tracker": "bytetrack", "model": "crema-swin-int8", "device": "cpu",
             "clip_frames": 8, "emotion_hop_ms": null, "detection_frequency": null, "identification": true},
  "audio":  {"model": "crema-wavlm-int8", "device": "cpu", "vad_threshold": null, "window_ms": null, "hop_ms": null},
  "fusion": {"method": "stack_hidden", "voice_max_age_ms": null, "min_overlap_ms": null}
}
```

`null` — значение по умолчанию сервиса. Удаление конфига — tombstone (пустое значение).

### `session-commands` и `session-status` (`ingest/contracts.py`)

Команда — `{action: "start" | "stop", camera_id, issued_at, spec}`, где `spec` — `SessionSpec` (тип, имя, источник
или ключ загрузки, частота и размер кадра, модальности, настройки API для показа). Статус —
`{camera_id, state, spec, stats, frames_sent, errors, error, ingest_id, updated_at, ended_at}`, `state` —
`starting`, `live`, `running`, `done`, `offline` или `error`.

### `vision-analytics` (schema 2)

```json
{
  "camera_id": "upload_1e3934", "frame_id": "120", "seq": 120, "pts_ms": 6000,
  "capture_wall_ts": 1792755206.1, "processed_wall_ts": 1792755206.15,
  "label_set": "crema-d-6", "emotion_labels": ["anger", "disgust", "fear", "happy", "neutral", "sad"],
  "faces": [{
    "bbox": [x1, y1, x2, y2], "track_id": 1, "face_id": "uuid или pending-…",
    "emotion": "happy", "emotion_scores": [6 чисел], "predicted": false, "window_seq": 4,
    "emotion_window": {"seq": 4, "start_pts_ms": 3000, "end_pts_ms": 6000, "frames": 8, "model": "crema-swin-int8",
                       "logits": [6], "probs": [6], "emb": [256]}
  }]
}
```

`emotion` равно `null`, если оценки ещё нет или уверенность ниже порога. `predicted = true` — рамка
спрогнозирована трекером без детекции. `emotion_window` есть только на кадре, где посчитано новое окно.

### `audio-analytics` (schema 2)

`window_seq`, `pts_start_ms`, `pts_end_ms`, `speech_prob`, `is_speech`; при речи — `scores`, `label`,
`logits`, `emb`; всегда — сглаженные `smoothed_scores` и `smoothed_label`, а также `model`.

### `fused-analytics`

Сообщение `vision-analytics` без `emotion_window`, где у каждого лица добавлены:

| Поле | Смысл |
|---|---|
| `emotion`, `emotion_scores` | итог после слияния и сглаживания |
| `face_emotion`, `face_scores` | только лицо |
| `voice_attributed`, `voice_emotion`, `voice_scores`, `voice_age_ms` | голос, приписанный лицу |
| `fusion_used`, `fusion_seq` | что реально слито и номер слияния трека |

На уровне кадра: `fusion` (метод, режим `single_face` / `multi_face` / `no_face`, параметры),
`scene_voice` (последняя оценка голоса сцены: `label`, `scores`, `age_ms`, `speech_prob`, `speaking`) и
`fused_wall_ts`.

### WebSocket браузера

| Канал | Направление | Формат |
|---|---|---|
| `/api/live/{id}` | сервер → браузер | `[uint32 BE длина заголовка][JSON {pts_ms, seq, frame_id, capture_wall_ts, width, height}][JPEG]` |
| `/api/events/{id}` | сервер → браузер | JSON `{"kind": "analytics", "data": <fused-analytics>}`, раз в 15 с `{"kind": "ping"}` |
| `/api/capture/{id}` | браузер → сервер | `[uint8 тип][uint32 BE pts_ms][данные]`: тип 1 — JPEG кадра, 2 — PCM s16le 16 кГц моно |

## 5. База данных

Таблицы и миграции — в [`modules/db_managing.md`](modules/db_managing.md). Запись `emotion_timeseries.data`:

```json
{
  "camera_id": "upload_1e3934", "track_id": 1, "modality": "face",
  "started_at": 1792755203.2, "ended_at": 1792755230.0,
  "label_set": "crema-d-6", "labels": ["anger", "disgust", "fear", "happy", "neutral", "sad"],
  "samples": [{"t": 1792755206.2, "label": "happy", "scores": [6 чисел]}]
}
```

Записи старых моделей (`label_set = legacy-8`) остаются читаемыми: порядок классов берётся из
`labels` или из `LEGACY_LABELS`.

## 6. Конфигурация

Значения по умолчанию — в `docker-compose.yml` и `.env.example`; для нескольких машин — `deploy/docker-stack.yml`
(см. [DEPLOYMENT.md](DEPLOYMENT.md)). Главное:

| Сервис | Переменные |
|---|---|
| все | `KAFKA_BOOTSTRAP_SERVERS`, `TOPIC_*`, `OPENCV_NUM_THREADS`, `PROCESS_PRIORITY`, `HEARTBEAT_INTERVAL_S`, `HEALTH_FILE` |
| consumer | `EMOTION_MODEL_DEFAULT`, `EMOTION_NUM_THREADS`, `DETECTOR_BACKEND`, `DETECTOR_MIN_CONF`, `DETECTOR_MAX_SIDE`, `DETECTION_FREQUENCY`, `CLIP_FRAMES`, `EMOTION_HOP_MS`, `CLIP_WINDOW_MS`, `TRACKER_DEFAULT`, `IDENTIFICATION_ENABLED`, `IDENTIFIER_THRESHOLD`, `MAX_FRAME_AGE_MS`, `DB_*` |
| audio-consumer | `VOICE_MODEL_DEFAULT`, `VOICE_NUM_THREADS`, `AUDIO_WINDOW_MS`, `AUDIO_HOP_MS`, `VAD_THRESHOLD`, `VAD_THRESHOLD_DBFS`, `VAD_BACKEND`, `VAD_MODEL_PATH`, `VAD_FRAME_THRESHOLD`, `MAX_CHUNK_AGE_MS` |
| fusion | `FUSION_METHOD`, `FUSION_VOICE_MAX_AGE_MS`, `FUSION_MIN_OVERLAP_MS`, `FUSION_DB_ENABLED`, `FUSION_TRACK_TIMEOUT_S`, `DB_*` |
| archiver | `ARCHIVE_SPOOL_DIR`, `ARCHIVE_ANALYTICS_GRACE_S`, `ARCHIVE_IDLE_TIMEOUT_S`, `ARCHIVE_MAX_DURATION_S`, `ARCHIVE_RENDER_THREADS`, `S3_*`, `DB_*` |
| webapp | `AFFECTRA_KAFKA`, `AFFECTRA_TOPIC_*`, `AFFECTRA_DB_*`, `AFFECTRA_REQUIRE_READY`, `S3_*` |
| ingest | `TOPIC_SESSION_COMMANDS`, `TOPIC_SESSION_STATUS`, `INGEST_GROUP_ID`, `INGEST_CACHE_DIR`, `INGEST_PORT`, `INGEST_ADVERTISED_ADDRESS`, `S3_*` |
| модели | `CREMA_MODELS_DIR` — каталог моделей CREMA-D (по умолчанию `models/crema`) |

## 7. Соглашения в коде

- Модульный докстринг — одна строка. Докстринги функций в стиле Google: краткое описание, `Args:` с
  выровненными колонками `имя (тип): описание`, `Raises:`, `Returns:`. Тривиальные методы без докстрингов.
- Комментарии — только там, где без них неясно «почему».
- Импорты: сначала `import x`, затем `from` стандартной библиотеки, затем сторонние пакеты, затем свои;
  одно имя на строку.
- Подряд идущие однострочные присваивания выравниваются по `=`.
- Сообщения Kafka — dataclass-контракты с `schema_version`; при чтении неизвестные поля игнорируются,
  чтобы новые поля не ломали старых потребителей.
- Сервис можно запустить без Kafka: `handle_*` вызываются напрямую — на этом построены тесты.
- `src/emotion_kit` перенесён из проекта обучения без изменений стиля, чтобы его было проще обновлять.

## 8. С чего начать чтение

1. `src/video_producing/contracts.py` и `src/session_config/contracts.py` — что ходит по топикам.
2. `src/frames_processing/processing_pipline.py`, метод `_handle_frame` — обработка кадра.
3. `src/audio_processing/pipeline.py`, метод `handle_chunk` — обработка звука.
4. `src/fusion/service.py`, методы `handle_vision` и `_fuse_track` — слияние.
5. `src/session_archive/service.py` — итоговая запись.
6. `src/web_server/services/session_manager.py` и `src/ingest/service.py` — как запускаются сессии.
7. `frontend/src/pages/HomePage.tsx` и `SessionPage.tsx` — интерфейс.
