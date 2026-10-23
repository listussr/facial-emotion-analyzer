# `session_config` — настройки сессий

Передать сервисам обработки настройки конкретной сессии (модели, трекер, частоту, метод слияния) без
запросов к веб-серверу и без поля в каждом кадре. Настройки живут в compacted-топике `session-config`,
ключ — `camera_id`.

```
src/session_config/
├── contracts.py  SessionConfig и секции VisionSettings, AudioSettings, FusionSettings
├── publisher.py  KeyedPublisher (общий), SessionConfigPublisher: запись с подтверждением, tombstone
├── store.py      CompactedTopicStore (общий), SessionConfigStore: локальная копия топика в фоновом потоке
└── topic.py      ensure_topic / check_topic: создание и проверка настроек топика
```

---

## Контракт

```python
SessionConfig(camera_id="upload_1e3934", revision=next_revision(None),
              vision=VisionSettings(model="crema-swin", clip_frames=16),
              audio=AudioSettings(model="crema-wavlm"),
              fusion=FusionSettings(method="stack_logits"))
```

| Секция | Поля | Читает |
|---|---|---|
| `vision` | `tracker`, `model`, `device`, `detection_frequency`, `clip_frames`, `emotion_hop_ms`, `identification` | consumer |
| `audio` | `model`, `device`, `vad_threshold`, `window_ms`, `hop_ms` | audio-consumer |
| `fusion` | `method`, `voice_max_age_ms`, `min_overlap_ms` | fusion |

- `None` в поле — значение по умолчанию сервиса. Сервис получает итог через
  `section.merged_over(defaults)`.
- Неизвестные поля и секции при чтении игнорируются: новые настройки не ломают старые сервисы.
- `revision` растёт с каждым изменением (`next_revision` = `max(старая + 1, time_ns())`). Хранилище никогда не
  заменяет конфиг конфигом с меньшей ревизией.

## Публикация

`SessionConfigPublisher` — продюсер с `acks=all` и идемпотентностью. Партиционер тот же, что у медиа, поэтому
конфиг лежит в партиции с тем же номером, что и кадры сессии.

- `publish(config)` ждёт подтверждения брокера; при таймауте — `PublishError`.
- `delete(camera_id)` пишет tombstone; после уплотнения конфиг исчезает из топика.

Веб-сервер публикует конфиг **до** запуска продюсера, а удаляет после `stream_end`, чтобы сервисы
дообработали последние кадры с настройками сессии.

## Чтение

`SessionConfigStore` читает топик с начала в фоновом потоке и держит словарь `camera_id → SessionConfig`.

- `start(ready_timeout)` ждёт, пока прочитан весь топик; если не успел — `StoreNotReadyError`, а сервис
  продолжает на значениях по умолчанию и подхватит конфиги позже.
- `get(camera_id)` — текущий конфиг; `wait_for(camera_id, timeout)` — с ожиданием: первый кадр может прийти
  раньше конфига.
- `set_on_change(callback)` — уведомление об изменении. По нему видеосервис сбрасывает вычисленные настройки
  и при смене трекера пересоздаёт трекер, а аудиосервис пересоздаёт буфер камеры.

## Общие классы

`CompactedTopicStore` и `KeyedPublisher` не привязаны к конфигам: наследник хранилища задаёт `_decode` и
`_is_newer`. На них же построены статусы сессий сервиса приёма (`SessionStatusStore` в топике
`session-status`, порядок по `updated_at`) и команды (`session-commands`). `KeyedPublisher.send` ждёт
подтверждения, `send_async` — нет (для частых статусов). `wait_for(key, timeout, predicate)` ждёт не просто
запись, а запись, удовлетворяющую условию — так веб-сервер ждёт ответа узла приёма на команду `start`.

## Топик

`ensure_topic` создаёт `session-config` с `cleanup.policy=compact` и короткими сегментами; у существующего
только сверяет настройки и пишет расхождения. В Docker топики создаёт `topics-init`, а автосоздание топиков
в Kafka выключено: иначе ранний читатель создал бы `session-config` без уплотнения.

## CLI

`scripts/session_config_cli.py` — ручное управление конфигами: `init-topic`, `set`, `unset`, `get`, `list`,
`delete`. Пример:

```bash
python scripts/session_config_cli.py set cam_01 --vision tracker=deepsort clip_frames=16 --fusion method=mean_probs
```
