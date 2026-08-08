# `src/logging_config.py` — настройка логирования

Один модуль на все сервисы: каждый entry point первым делом зовёт
`setup_logger(<имя сервиса>)`, после чего обычный `logging.info(...)` из
любого места кода попадает в общий формат.

---

## `setup_logger(service_name, log_level=None, json_format=True)`

```python
from src.logging_config import setup_logger

setup_logger("processing")                      # JSON (по умолчанию)
setup_logger("webserver", json_format=False)    # цветной текст
```

| Параметр | Смысл |
|---|---|
| `service_name` | Попадает в каждую запись — по нему различаются сервисы в общем потоке логов |
| `log_level` | Если не задан, берётся `LOG_LEVEL` из окружения, иначе `INFO` |
| `json_format` | `True` — `JSONFormatter`, `False` — `ColoredFormatter` |

Функция настраивает **корневой** логгер: чистит существующие хендлеры и
вешает один `StreamHandler` в `stdout`. Поэтому вызывать её нужно ровно один
раз и как можно раньше — библиотечные логгеры (`confluent_kafka`,
`urllib3`) подхватят те же настройки.

Кто какой формат использует:

| Сервис | Вызов |
|---|---|
| `consumer.py` | `setup_logger("processing")` — JSON |
| `visualizer.py` | `setup_logger("visualizer")` — JSON |
| `producer.py` | `setup_logger("producing")` — JSON |
| `web_server/main.py` | `setup_logger("webserver", json_format=False)` — цветной текст |

---

## Форматтеры

### `JSONFormatter`

Одна строка JSON на запись:

```json
{
  "timestamp": "2026-05-12 18:41:03,221",
  "service": "processing",
  "pid": 24188,
  "level": "INFO",
  "message": "Analyzed 100 frames (47.7 FPS over last 100) [in_q=0/4, out_q=0/4]",
  "trace_id": "",
  "filename": "processing_pipline.py",
  "lineno": 605,
  "funcName": "process"
}
```

При `logging.exception(...)` / `exc_info=True` добавляется поле
`exception` с полным traceback. Формат удобен для `docker logs` и любого
сборщика логов.

### `ColoredFormatter`

Человекочитаемая строка с ANSI-подсветкой уровня:

```
2026-05-12 18:41:03,221 - [webserver] - PID=24188 - INFO - session_manager.py:197 - Session started: cam_ab12cd (camera, Ресепшн)
```

Цвета: DEBUG — голубой, INFO — зелёный, WARNING — жёлтый, ERROR — красный,
CRITICAL — фиолетовый. `colorama.init()` вызывается при импорте модуля,
чтобы подсветка работала в консоли Windows.

---

## `trace_id`

`trace_id_var = contextvars.ContextVar("trace_id", default="")` — заготовка
под сквозную трассировку запроса: значение попадает в JSON-логи, но пока
нигде не устанавливается (поле всегда пустое). Чтобы включить трассировку,
достаточно вызывать `trace_id_var.set(...)` в начале обработки —
`contextvars` корректно переживает `await` и не течёт между задачами.

---

## Особенности

- **Переменная `trace_id_var` объявлена дважды** (строки 11 и 22) — вторая
  перетирает первую, поведение от этого не меняется.
- **`ColoredFormatter.format` мутирует `record.levelname`**, добавляя
  ANSI-коды. Если после этого форматтера повесить второй хендлер, он получит
  запись с уже вшитой раскраской.
- **Формат времени** — стандартный `logging.Formatter.formatTime`
  (локальное время, миллисекунды через запятую), не ISO-8601.
- **`setup_logger` перезаписывает хендлеры.** Повторный вызов из библиотеки
  или теста «отключит» ранее настроенное логирование.
