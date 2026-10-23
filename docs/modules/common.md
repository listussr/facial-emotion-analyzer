# Общие модули

Небольшие модули, которыми пользуются все сервисы.

---

## `src/emotion_labels.py` — классы эмоций

Единый набор из 6 классов CREMA-D для всех модальностей: i-й выход любой модели соответствует
`EMOTION_LABELS[i]`.

```python
EMOTION_LABELS = ("anger", "disgust", "fear", "happy", "neutral", "sad")
NUM_EMOTIONS   = 6
LABEL_SET      = "crema-d-6"
```

`label_of(idx)` — метка по индексу (пустая строка вне диапазона). `LABEL_SET` пишется в сообщения и
таймсерии, чтобы старые записи на 8 классов (`legacy-8`) читались правильно. Интерфейс использует тот же
порядок (`CURRENT_EMOTIONS` в `frontend/src/types/index.ts`).

## `src/cpu_threads.py` — потоки и приоритет

- `limit_opencv_threads(default=1)` — число потоков OpenCV из `OPENCV_NUM_THREADS`. Лишние потоки почти не
  ускоряют YuNet, resize и JPEG на кадрах 480–854 пикселей, но в разы увеличивают общую нагрузку на CPU и
  отнимают ядра у моделей. Вызывается всеми сервисами, работающими с изображениями.
- `lower_priority(default="low")` — понижает приоритет процесса (`BELOW_NORMAL` на Windows, `nice 10` в
  Linux), если `PROCESS_PRIORITY` не `normal`. Его вызывают фоновые сервисы: audio-consumer, visualizer,
  archiver. Видеопайплайн получает CPU первым.

## `src/logging_config.py` — логи

`setup_logger(service_name, log_level=None, json_format=True)` настраивает корневой логгер:

- **JSON** (по умолчанию у сервисов): одна строка на запись с `timestamp`, `service`, `pid`, `level`, `message`,
  `filename`, `lineno`, `funcName` — удобно для сбора логов;
- **цветной текст** (`json_format=False`, у веб-сервера): для чтения в терминале.

Уровень — `log_level` или `LOG_LEVEL` (по умолчанию `INFO`).

## `src/object_storage.py` — объектное хранилище

`S3Settings.from_env()` (`S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION`) и `S3Storage` —
тонкая обёртка boto3 над одним бакетом: `ensure_bucket`, `upload_file`, `upload_fileobj` (с метаданными
объекта), `download_file`, `list_keys`, `head`, `get(key, byte_range)`, `delete`, `delete_prefix`. Адресация
path-style и подпись s3v4 — работает с любым S3-совместимым хранилищем. Используется веб-сервером (загрузки,
записи), сервисом приёма (скачивание загрузок) и архиватором.

| Префикс ключа | Что лежит |
|---|---|
| `uploads/<id>.<ext>` | загруженные файлы; в метаданных исходное имя (URL-кодированное), дорожки, длительность |
| `sessions/<id>/recording.mp4`, `analytics.json` | итоговые записи сессий |

## `src/data_models/` — устаревшие dataclass'ы

`FrameModel`, `DetectedFaceModel`, `EmotionModel` — описания сообщений первой версии системы. Код их больше не
использует: сообщения описаны контрактами в `video_producing/contracts.py`, `audio_processing/contracts.py`
и `session_config/contracts.py`, а форматы аналитики — в [`CODE_OVERVIEW.md`](../CODE_OVERVIEW.md).
