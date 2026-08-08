# Архитектура Affectra

Документ описывает архитектуру сервиса по слоям и поясняет принятые
проектные решения. Диаграммы — Mermaid, рендерятся на GitHub и
экспортируются в SVG для тезисной работы.

---

## 1. Слои системы

Affectra — это пять независимых процессов, обменивающихся через Apache
Kafka, и одно общее хранилище состояния (Postgres + pgvector). Такая
декомпозиция даёт три практических свойства:

1. **Изоляция отказов.** Падение аннотатора не останавливает запись
   таймсерий; падение веб-сервера не прерывает обработку.
2. **Произвольная масштабируемость.** Каждую стадию можно вынести на
   отдельный хост, поделить топик по партициям.
3. **Замена алгоритмов без остановки.** Стадии слабо связаны — детектор
   видит только кадр, трекер только bbox-ы, аннотатор только результаты.

```mermaid
flowchart TB
    subgraph "Захват"
        P1["CameraProducer #1<br/>(RTSP / file / device 0)"]
        P2["CameraProducer #2"]
        PN["CameraProducer #N"]
    end

    subgraph "Шина Kafka"
        R[raw-video-frames]
        A[vision-analytics]
        AN[annotated-video]
    end

    subgraph "Обработка"
        PIPE["ProcessingPipeline<br/>(consumer.py)"]
        ANN["VideoAnnotator<br/>(visualizer.py)"]
    end

    subgraph "Доступ"
        WS["Web Server<br/>(webapp.py / FastAPI)"]
        UI["Browser UI<br/>(React)"]
    end

    subgraph "Хранилище"
        DB[(Postgres + pgvector)]
    end

    P1 --> R
    P2 --> R
    PN --> R
    R --> PIPE
    PIPE --> A
    PIPE <--> DB
    R --> ANN
    A --> ANN
    ANN --> AN
    AN --> WS
    A --> WS
    WS <--> DB
    UI <--> WS
```

Топики Kafka и их роль:

| Топик | Кто пишет | Кто читает | Содержимое |
|---|---|---|---|
| `raw-video-frames` | `CameraProducer` | `ProcessingPipeline`, `VideoAnnotator`, web-server | msgpack: `{camera_id, frame_id, frame_data (JPEG), session_config, ...}` |
| `vision-analytics` | `ProcessingPipeline` | `VideoAnnotator`, web-server (WS-канал) | JSON: `{camera_id, frame_id, faces: [{bbox, track_id, face_id, emotion, emotion_scores}]}` |
| `annotated-video` | `VideoAnnotator` | web-server (MJPEG-эндпоинт) | msgpack: `{camera_id, frame_id, annotated_frame (JPEG с нанесёнными bbox-ами)}` |

---

## 2. Стадии обработки

`ProcessingPipeline._handle_frame` обрабатывает один кадр в пять
последовательных шагов. Каждый шаг — отдельный модуль; контракт между
ними намеренно узкий, что позволило менять алгоритмы без правки
остального кода.

```mermaid
flowchart LR
    A["1. Захват<br/>cv2.imdecode JPEG"]
    B["2. Детекция<br/>MediaPipe → geometry filter<br/>min_confidence, ratio, area"]
    C["3. Трекинг<br/>get_tracker(session) → DeepSORT | ByteTrack"]
    D["4. Идентификация<br/>FaceNet (async) → pgvector"]
    E["5. Эмоции<br/>batch inference → softmax<br/>EMA + hysteresis smoothing"]
    F["6. Аннотация результата<br/>(faces[].bbox, track_id, face_id, emotion, emotion_scores)"]
    G["7. Сохранение<br/>JSONB row при эвикции трека"]

    A --> B --> C --> D --> E --> F --> G
```

### 2.1. Детекция (`face_detection`)

Используется MediaPipe Face Detection (модель `blaze_face_short_range`).
Кадр предварительно уменьшается до `scale=0.5` для скорости; bbox'ы
масштабируются обратно. Геометрические фильтры отсекают шум:

- `width, height ≥ 40 px`
- `0.75 ≤ height/width ≤ 1.7`
- `0.002 · frame_area ≤ bbox_area ≤ 0.5 · frame_area`

Фильтр `fast_face_filter` дополнительно проверяет, что у изображения
есть достаточная градиентная структура — отсекает однородные ложно-
положительные срабатывания.

### 2.2. Трекинг (`faces_tracking`)

Два алгоритма за общим интерфейсом `_Tracker` (см.
`faces_tracking/tracker.py`):

- **DeepSORT** — Kalman + Re-ID эмбеддер (MobileNet). Лучше через
  перекрытия, но даёт ~10× нагрузку на CPU.
- **ByteTrack** — motion-only, без CNN. На i5-1135G7 в ~3× быстрее.

Для пайплайна каждая сессия (`camera_id`) получает **свой** инстанс
трекера — состояние (Kalman, ID-counter) уникально для сессии.

### 2.3. Идентификация (`face_identification`)

Поток построен с упором на то, что инференс FaceNet и поиск в pgvector
**не должны блокировать стадию B пайплайна**. Алгоритм:

1. Пайплайн встретил новый трек → вставил cached-запись с
   `face_id = "pending-init"` и сразу продолжил.
2. Параллельно отправил задачу в однопоточный `_db_executor` внутри
   `FaceIdentifier`.
3. Воркер считает 512-мерный эмбеддинг (Inception-ResNetV1, веса
   VGGFace2), ищет ближайшего соседа в `face_embeddings` через
   `embedding <=> %s::vector` (cosine). Если similarity ≥ threshold
   (по умолчанию 0.4) — берём существующий UUID; иначе вставляем
   новый.
4. По завершении воркер вызывает `on_resolved(real_id)` — пайплайн
   обновляет `cached["face_id"]` атомарно (GIL гарантирует).

При завершении трека таймсерия эмоций сбрасывается в
`emotion_timeseries` с этим самым `face_id`. Pending-id игнорируются.

### 2.4. Эмоции (`emotion_recognition`)

8 классов: `anger, contempt, disgust, fear, happy, neutral, sad, surprise`.
Шесть архитектур; CPU-инференс через ONNX Runtime, GPU — через PyTorch.

```mermaid
classDiagram
    class _Recognizer {
        +predict(images) np.ndarray
    }
    class _RecognizerCPU {
        -_session : ort.InferenceSession
        -_input_h, _input_w : int
    }
    class _RecognizerCuda {
        -model : nn.Module
        -_transform : transforms.Compose
        -_device : torch.device
    }
    class EmotionRecognizer {
        <<factory>>
        +__new__(model, device, num_threads) _Recognizer
    }
    _Recognizer <|-- _RecognizerCPU
    _Recognizer <|-- _RecognizerCuda
    EmotionRecognizer ..> _RecognizerCPU : creates
    EmotionRecognizer ..> _RecognizerCuda : creates
```

Сглаживание — **EMA + гистерезис** (`pipeline_utils.py`). EMA удерживает
скользящее среднее по вероятностям; гистерезис требует подтверждения
смены лидера N раз подряд, чтобы лента эмоций не «дёргалась».

Эмоции считаются **батчем по всем лицам кадра** — один прогон модели
вместо N последовательных. На сессиях с несколькими лицами даёт
50–80% буст к FPS.

### 2.5. Аннотация и сохранение

После всех стадий пайплайн пишет:

1. В Kafka `vision-analytics` — JSON для аннотатора и WS-канала.
2. В Postgres `emotion_timeseries` — JSONB-блок таймсерии, **только
   когда трек уходит из кэша** (по `_INACTIVITY_TIMEOUT = 5 секунд`),
   с агрегацией всех точек этого трека за время его жизни.

---

## 3. Per-session routing

В одной инсталляции Affectra одновременно могут жить сессии с разными
алгоритмами. Например, веб-камера ресепшна на ByteTrack + ResNet-18 INT8
(быстро, шумно, ID-switch допустимы) и загруженное видео интервью на
DeepSORT + ConvNeXt (точно, ID стабильны).

Конкретный путь конфигурации через систему:

```mermaid
sequenceDiagram
    autonumber
    participant U as Frontend
    participant W as web_server
    participant SM as SessionManager
    participant CP as CameraProducer
    participant K as Kafka
    participant P as ProcessingPipeline

    U->>W: POST /api/sessions/camera<br/>{name, source, config: {tracker, model, device}}
    W->>SM: create_camera(...)
    SM->>SM: _probe_source (cv2.VideoCapture)
    SM->>CP: CameraConfig(tracker_name, emotion_model, compute_device, ...)
    SM->>CP: producer.start()  (отдельный thread)
    SM-->>W: SessionInfo
    W-->>U: 200 OK

    loop каждый кадр
        CP->>K: msgpack {camera_id, frame_data, session_config}
        K->>P: poll
        P->>P: tracker = _get_session_tracker(camera_id, session_config)
        Note over P: создаётся лениво,<br/>тип берётся из session_config
        P->>P: recognizer = _get_recognizer(session_config)
        Note over P: пул по (model, device, threads) —<br/>одинаковые модели шарятся
        P->>P: detect → track → identify → emotion
        P->>K: vision-analytics {faces[]}
    end

    Note over P: когда трек уходит из кэша →<br/>_flush_emotion_timeseries → Postgres
```

Реализация — в `ProcessingPipeline._get_session_tracker` и
`_get_recognizer`. Стоимость:

- **Трекеры**: лёгкие (Kalman + IoU), память ~1 МБ на сессию.
- **Распознаватели**: тяжёлые (45–110 МБ для FP32), поэтому **шарятся**
  по ключу `(model, device, num_threads)`. Две сессии с одинаковой
  моделью грузят её один раз.

---

## 4. Threaded pipeline (Stage A / B / C)

`ProcessingPipeline.process` запускает три стадии в отдельных потоках,
связанные ограниченными очередями. Это даёт прирост FPS, когда
producer обгоняет процессор: Stage A декодирует следующий кадр, пока
Stage B считает текущий, а Stage C параллельно публикует предыдущий.

```mermaid
flowchart LR
    K1[(Kafka<br/>raw-video-frames)]
    A["Stage A (thread)<br/>consume + msgpack + cv2.imdecode"]
    Q1[/in_q<br/>maxsize=4/]
    B["Stage B (main)<br/>_handle_frame:<br/>detect / track / identify / emotion"]
    Q2[/out_q<br/>maxsize=4/]
    C["Stage C (thread)<br/>JSON encode + Kafka produce"]
    K2[(Kafka<br/>vision-analytics)]

    K1 --> A --> Q1 --> B --> Q2 --> C --> K2
```

**Backpressure** обеспечивается ограниченными очередями: если Stage B
отстаёт, Stage A блокируется на `put`, чтение из Kafka замедляется до
скорости обработки. Без этого память росла бы при медленном Stage B.

Контроль в бенчмарке: `bytetrack_resnet18_t7_seq` (без pipelining) даёт
53 FPS, с pipelining `bytetrack_resnet18_t7` — 48 FPS. На текущем
видео producer не обгоняет процессор, и pipelining добавляет ~5%
overhead. Это нормально и обсуждено в разделе «Оптимизация»: pipelining
включается, когда производитель быстрее потребителя.

---

## 5. Схема Postgres

```mermaid
erDiagram
    face_embeddings ||--o{ emotion_timeseries : "user_id"
    face_embeddings {
        serial id PK
        uuid user_id UK "gen_random_uuid()"
        vector(512) embedding "ivfflat cosine_ops"
        timestamptz first_seen
        bytea face_image "JPEG-кроп лица"
    }
    emotion_timeseries {
        serial id PK
        uuid user_id FK
        jsonb data "samples, started/ended_at, camera_id, track_id"
        timestamptz created_at
    }
```

Индекс `idx_face_embeddings` — IVFFlat с метрикой cosine для поиска
ближайших соседей. При 100+ лицах в базе ускоряет k-NN на порядок.

Запросы — в `db_managing/_faces_db_handler.py` и
`web_server/routes/history.py`.

---

## 6. Frontend

Single-page React 18 приложение, организованное вокруг роутера:

```
/                 — главная (превью активных сессий)
/cameras          — сетка камер (1×1 / 2×2 / 3×3)
/cameras/:id      — фокус камеры
/uploads          — загрузка файлов + сетка
/uploads/:id      — фокус видеофайла
/history          — поиск по фото → top-K похожих
/history/:userId  — детальная история эмоций
/settings         — управление камерами и параметрами
/about            — этика и обзор системы
```

Состояние:
- **REST-поллинг** (`useSessions`, `useUiPrefs`) — каждые 2.5–5 секунд.
- **WebSocket** (`useSessionEvents`) — live-события аналитики (~25 Hz).
- **localStorage** — UI-предпочтения (число похожих лиц при поиске).

Графики (`components/charts/`) — собственные SVG-компоненты без
сторонних библиотек: `EmotionBars`, `EmotionTimeline`,
`EmotionLines` (линии вероятностей), `EmotionAreaChart` (stacked area),
`EmotionRadar` (polar профиль). Сэмплы прорежены до 600 точек для
плавности.

---

## 7. Принятые проектные решения и компромиссы

| Решение | Альтернатива | Аргумент |
|---|---|---|
| Kafka между сервисами | gRPC / HTTP | Декаплинг отказов; уже есть как infra |
| msgpack для кадров | JSON | Бинарные JPEG → msgpack ≈ 2× меньше overhead, чем base64 |
| Stage A/B/C в одном процессе | разные процессы | Сложность networking-а перевешивает выигрыш для текущей нагрузки |
| Сглаживание EMA+гистерезис | argmax-каждый-кадр | Стабильная лента эмоций без флика; pacing setting `emotion_frequency` |
| Per-session lazy spawn | глобальная очередь | Тяжёлые модели грузятся только когда нужны; рекомендации стабильны |
| pgvector | FAISS / Elasticsearch | Один сервис, общая транзакция с face_image |
| ONNX Runtime + XNNPACK | TensorRT | Кроссплатформенно; INT8-выигрыш на VNNI CPU |
| MJPEG для UI | WebRTC / HLS | Простота; `<img>` рендерит нативно; latency ~50 мс ок для демо |

---

## 8. Дальнейшее развитие (упомянуть в work plan)

- Static INT8 quantization с калибровочным датасетом — даст реальный
  выигрыш для свёрточных моделей на VNNI CPU.
- Поддержка TensorRT на GPU.
- Multi-camera fusion: совместный треккинг лиц через несколько камер.
- Аналитика поверх `emotion_timeseries`: тренды, аномалии, групповая
  агрегация настроения.
- Полноценная аутентификация в веб-интерфейсе (сейчас локальная сеть).
