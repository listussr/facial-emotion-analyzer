# `src/data_models` — модели данных

Небольшой справочный модуль: dataclass-описания сущностей, которые ходят
между сервисами.

```
src/data_models/
├── __init__.py          — пустой (экспортов нет, импортировать по файлам)
├── frame_data.py        — FrameModel
├── detection_data.py    — DetectedFaceModel
└── emotion_data.py      — EmotionModel
```

---

## Классы

### `FrameModel` ([frame_data.py](../../src/data_models/frame_data.py))

```python
@dataclass
class FrameModel:
    frame_id: str
    camera_id: str
    timestamp: float
    frame_data: bytes   # JPEG
    width: int
    height: int
```

Соответствует ядру сообщения в топике `raw-video-frames`.

### `DetectedFaceModel` ([detection_data.py](../../src/data_models/detection_data.py))

```python
@dataclass
class DetectedFaceModel:
    track_id: str
    frame_id: str
    camera_id: str
    bbox: List[float]
    confidence: float
```

Одно обнаруженное и отслеживаемое лицо.

### `EmotionModel` ([emotion_data.py](../../src/data_models/emotion_data.py))

```python
@dataclass
class EmotionModel:
    track_id: str
    frame_id: str
    emotion: str
    confidence: float
    timestamp: float
```

Одно измерение эмоции.

---

## Статус модуля

Классы **не используются в рантайме**: и продюсер, и пайплайн, и аннотатор
работают с обычными словарями, потому что данные всё равно проходят через
msgpack/JSON, а лишняя сериализация dataclass ↔ dict стоила бы времени на
горячем пути.

Ценность модуля — документирующая: это компактная запись того, какие поля
считаются обязательными. Фактические форматы сообщений «как есть» описаны в
[CODE_OVERVIEW §4](../CODE_OVERVIEW.md#4-форматы-сообщений), а валидируемые
модели API — в [`web_server/schemas.py`](web_server.md).

Если модуль решат ввести в работу, стоит держать в голове два расхождения с
реальностью: `bbox` в сообщениях — это `[x1, y1, x2, y2]` в `int`, а
`emotion` сопровождается полным вектором `emotion_scores` из восьми
вероятностей, а не одним `confidence`.
