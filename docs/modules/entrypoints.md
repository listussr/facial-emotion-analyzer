# Точки входа

В корне репозитория лежат восемь скриптов запуска. Каждый читает настройки из переменных окружения,
собирает объекты из `src/` и запускает бесконечный цикл обработки. В Docker все они живут в одном образе
(`Dockerfile.python`) и отличаются командой в `docker-compose.yml`.

| Скрипт | Цикл | Читает | Пишет |
|---|---|---|---|
| `webapp.py` | uvicorn + FastAPI | `session-status`, `fused-analytics`, `raw-video-frames`, `annotated-video` | `session-config`, `session-commands`, БД, S3 |
| `ingest.py` | uvicorn + `IngestService.process` | `session-commands`, `session-status`, WebSocket браузера, S3 | медиа и события сессий, `session-status` |
| `consumer.py` | `ProcessingPipeline.process` | `raw-video-frames`, `session-events`, `session-config` | `vision-analytics`, БД |
| `audio_consumer.py` | `AudioPipeline.process` | `raw-audio-chunks`, `session-events`, `session-config` | `audio-analytics` |
| `fusion_consumer.py` | `FusionService.process` | `vision-analytics`, `audio-analytics`, `session-events`, `session-config` | `fused-analytics`, БД |
| `visualizer.py` | `VideoAnnotator.run` | `raw-video-frames`, `fused-analytics` | `annotated-video` |
| `archiver.py` | `ArchiveService.process` | `raw-video-frames`, `raw-audio-chunks`, `fused-analytics`, `session-events` | S3, БД |
| `producer.py` | `CameraProducer` | источник | медиа и события |

---

## `webapp.py`

Ограничивает потоки OpenCV и запускает `uvicorn` с `src.web_server.main:app` на порту 8000. Сессии запускает
командами сервису приёма. Настройки — `AFFECTRA_*` и `S3_*` (см. [web_server.md](web_server.md)).

## `ingest.py`

Сервис приёма медиа: `IngestService` (цикл команд и статусов в фоновом потоке) и FastAPI-приложение с
WebSocket камеры браузера и `/health` на порту `INGEST_PORT` (8001). Настройки — `TOPIC_SESSION_COMMANDS`,
`TOPIC_SESSION_STATUS`, `INGEST_GROUP_ID`, `INGEST_CACHE_DIR`, `S3_*` (см. [ingest.md](ingest.md)).
Несколько узлов приёма делят сессии через группу Kafka, а WebSocket браузера, попавший не на тот узел,
пересылается владельцу сессии (`INGEST_ADVERTISED_ADDRESS`).

## `consumer.py`

Видеопайплайн. Собирает настройки детектора, модели, трекера и идентификации, запускает хранилище
конфигов сессий и `ProcessingPipeline`.

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `DETECTOR_BACKEND` | `yunet` | `yunet` или `mediapipe` |
| `DETECTOR_MIN_CONF` | `0.6` | порог уверенности детектора |
| `DETECTOR_MAX_SIDE` | `480` | длинная сторона кадра для YuNet |
| `DETECTION_FREQUENCY` | `2` | детектор раз в N кадров |
| `EMOTION_MODEL_DEFAULT` | `crema-swin-int8` | модель лица по умолчанию |
| `EMOTION_NUM_THREADS` | `2` | потоки ONNX Runtime модели лица |
| `CLIP_FRAMES` | `8` | кадров на окно (8 или 16) |
| `EMOTION_HOP_MS` / `CLIP_WINDOW_MS` | `1000` / `3000` | шаг и длина окна эмоций |
| `TRACKER_DEFAULT` | `bytetrack` | `bytetrack` или `deepsort` |
| `IDENTIFICATION_ENABLED` | `true` | узнавание людей через БД |
| `IDENTIFIER_THRESHOLD` | `0.4` | порог косинусного расстояния FaceNet |
| `MAX_FRAME_AGE_MS` | `1000` | кадры старше отбрасываются, `0` — не отбрасывать |
| `SESSION_CONFIG_READY_TIMEOUT` | `10` | ожидание чтения `session-config` при старте |

Если топик конфигов не готов, сервис работает на значениях по умолчанию и подхватывает конфиги позже.

## `audio_consumer.py`

Аудиопайплайн с пониженным приоритетом. `make_speech_vad()` создаёт Silero VAD, если
`VAD_BACKEND=silero` и файл модели на месте, иначе остаётся только энергетический VAD (с предупреждением
в логе).

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `VOICE_MODEL_DEFAULT` | `crema-wavlm-int8` | модель голоса |
| `VOICE_NUM_THREADS` | `2` | потоки ONNX Runtime |
| `AUDIO_WINDOW_MS` / `AUDIO_HOP_MS` | `3000` / `1000` | окно и шаг |
| `VAD_THRESHOLD` | `0.3` | доля речи в окне для запуска модели |
| `VAD_THRESHOLD_DBFS` | `-45` | порог громкости энергетического VAD |
| `VAD_BACKEND` | `silero` | `silero` или `energy` |
| `VAD_MODEL_PATH` | `models/vad/silero_vad.onnx` | файл Silero VAD |
| `VAD_FRAME_THRESHOLD` | `0.5` | порог вероятности речи в кадре Silero |
| `MAX_CHUNK_AGE_MS` | `2000` | отсев устаревших чанков |

## `fusion_consumer.py`

Сервис слияния и `FusionRecorder` для записи слитых таймсерий и голоса сессий в БД.

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `FUSION_METHOD` | `stack_hidden` | метод слияния по умолчанию |
| `FUSION_VOICE_MAX_AGE_MS` | `3000` | насколько старый голос можно взять для лица без окна |
| `FUSION_MIN_OVERLAP_MS` | `1000` | минимальное перекрытие окон лица и голоса |
| `FUSION_DB_ENABLED` | `true` | писать ли таймсерии в БД |
| `FUSION_TRACK_TIMEOUT_S` | `3` | через сколько секунд без трека его таймсерия записывается |
| `FUSION_MAX_FRAME_AGE_MS` | `2000` | отсев устаревших кадров |

## `visualizer.py`

Фоновый `VideoAnnotator` с пониженным приоритетом: сопоставляет кадры с аналитикой по `frame_id` и пишет
размеченные JPEG в `annotated-video`. В показе в реальном времени не участвует — там рамки рисует браузер.

## `archiver.py`

`ArchiveService` с клиентом S3 и фабрикой подключений к БД (`ARCHIVE_DB_ENABLED=false` — не писать
статусы). Настройки: `ARCHIVE_SPOOL_DIR` (`data/archive_spool`), `ARCHIVE_ANALYTICS_GRACE_S` (15),
`ARCHIVE_IDLE_TIMEOUT_S` (120), `ARCHIVE_MAX_DURATION_S` (1800), `ARCHIVE_RENDER_THREADS` (2), `S3_*`.

## `producer.py`

Продюсер из командной строки — для отладки без веб-сервера:

```bash
python producer.py --source data/sync_test.mp4 --modalities video,audio --stop-on-end
```

```bash
python producer.py --source 0 --modalities video,audio --audio-device 1
```

`--source` принимает путь к файлу, `rtsp://…`, номер вебкамеры или `mic`; `--list-devices` выводит
микрофоны. Конфиг сессии продюсер не публикует, поэтому сервисы обрабатывают такой поток на значениях по
умолчанию (конфиг можно задать через `scripts/session_config_cli.py`).

## Общие для сервисов переменные

| Переменная | Смысл |
|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | брокеры Kafka через запятую (`kafka:29092` в compose, `localhost:9092` с хоста; в кластере — все три) |
| `TOPIC_*` | имена топиков |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL |
| `OPENCV_NUM_THREADS` | потоки OpenCV, по умолчанию 1 |
| `PROCESS_PRIORITY` | `low` (по умолчанию у фоновых сервисов) или `normal` |
| `TOPIC_SERVICE_HEALTH`, `HEARTBEAT_INTERVAL_S`, `HEALTH_FILE` | сигнал живости и файл для healthcheck (см. [service_health.md](service_health.md)) |

Все сервисы, кроме `webapp`, сразу при запуске публикуют сигнал `starting`, а после загрузки моделей — `ready`.
| `CREMA_MODELS_DIR` | каталог моделей CREMA-D |
