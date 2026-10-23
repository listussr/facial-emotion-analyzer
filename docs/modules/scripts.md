# `scripts/` — утилиты

Скрипты запускаются из корня репозитория. Модульный докстринг каждого скрипта служит справкой `--help`.

| Скрипт | Назначение | Состояние |
|---|---|---|
| `session_config_cli.py` | ручное управление конфигами сессий в `session-config` | актуален |
| `make_sync_test.py` | тестовый ролик со вспышкой и бипом в начале каждой секунды | актуален |
| `check_audio_stream.py` | проверка медиапотоков сессии в Kafka и синхронизации звука с видео | актуален |
| `check_emotion_timeseries.py` | диагностика таймсерий в БД (только чтение) | актуален |
| `benchmark.py` | свип конфигов видеопайплайна: кадры/с, задержка, память | устарел: перебирает старые покадровые модели |
| `quantize_emotion_model.py` | INT8-квантизация старых покадровых ONNX-моделей | только для старых моделей |

---

## `session_config_cli.py`

Подкоманды `init-topic`, `set`, `unset`, `get`, `list`, `delete`; общий параметр `--kafka`.

```bash
python scripts/session_config_cli.py init-topic
```

```bash
python scripts/session_config_cli.py set cam_01 --vision tracker=deepsort clip_frames=16 --audio window_ms=2000
```

```bash
python scripts/session_config_cli.py get cam_01
```

- `set` берёт текущий конфиг сессии (или пустой), накладывает поля и публикует с новой ревизией;
- `unset` возвращает поля к значениям по умолчанию;
- `delete` пишет tombstone;
- `init-topic` создаёт топик с уплотнением или показывает расхождения настроек существующего.

Полезно вместе с `producer.py`, который сам конфиг не публикует.

## `make_sync_test.py` и `check_audio_stream.py`

Проверка синхронизации звука и видео от продюсера до Kafka:

```bash
python scripts/make_sync_test.py --out data/sync_test.mp4
```

```bash
python producer.py --source data/sync_test.mp4 --modalities video,audio --stop-on-end
```

```bash
python scripts/check_audio_stream.py --camera-id cam_01 --sync --wav out.wav
```

`check_audio_stream.py` читает топики с начала, проверяет непрерывность `seq` и монотонность `pts_ms`, склеивает
звук в WAV и с `--sync` находит вспышки на кадрах и бипы в звуке, печатая расхождение по `pts_ms`.

## `check_emotion_timeseries.py`

Сводка по `emotion_timeseries`: треки, длительность, распределение меток, при `--samples` — сами отсчёты.

```bash
python scripts/check_emotion_timeseries.py --limit 3 --samples
```

## `benchmark.py` (требует обновления)

Гоняет видеофайл через `ProcessingPipeline` в отдельных процессах для каждого конфига и пишет
`scripts/bench_results/results.csv`, `fps.png`, `latency.png`. Список конфигов (`DEFAULT_CONFIGS`) перебирает
старые покадровые модели (ResNet, ConvNeXt, EfficientNet, Swin на 8 классов), которые видеопайплайн больше не
загружает. Его нужно перевести на текущие параметры: частота детекции, `crema-swin` / `crema-swin-int8`,
8 или 16 кадров на окно, число сессий. Результаты в `bench_results/` относятся к старой версии системы.

## `quantize_emotion_model.py`

Динамическая INT8-квантизация старых покадровых моделей из
`src/frames_processing/processing/emotion_recognition/models/`. Для моделей CREMA-D не нужен: их int8-версии
поставляются готовыми в `models/crema/`.
