# `affectra_mcp` — MCP-сервер `affectra-data`

Готовые «ручки» для агента-аналитика ([AGENT.md](../AGENT.md#5-mcp-сервер-affectra-data)). Модель не пишет
запросов: она вызывает ручки, внутри которых чтение S3 и БД уже написано. Сервер привязан к одному разбору и
работает **только на чтение** — это обеспечивают права, а не промпт.

```
src/affectra_mcp/
├── tools.py       AffectraTools — ручки; SessionContext; ToolError
├── server.py      create_server → MCPServer (официальный Python MCP SDK, mcp 2.x)
├── data.py        RecordingSource: series.json из S3, метаданные из reader.sessions
└── formatting.py  компактные таблицы, шаг ряда, «гнев 62%, нейтр 30%»
mcp_server.py      точка входа (stdio)
```

---

## Запуск

```bash
python mcp_server.py --session upload_ab12cd --privacy local --profile interview
```

```bash
python mcp_server.py --series path/to/series.json
```

Второй вариант — для отладки без хранилища и БД. Агент запускает сервер дочерним процессом на каждый разбор и
общается с ним по stdio. Логи идут в stderr, потому что stdout занят протоколом. Для отладки годится
MCP Inspector: `npx @modelcontextprotocol/inspector python mcp_server.py --series …`.

## Ручки

| Ручка | Что отдаёт |
|---|---|
| `get_session_overview()` | длительность, доли времени с лицом и речью, ведущие эмоции по модальностям, качество |
| `get_profile()` | пороги профиля сценария |
| `get_emotion_series(from_s, to_s, modality, step_s)` | таблица `t_s, гнев, …, грусть, лицо`; `modality` = `fused`, `face` или `voice`; не больше 120 строк — шаг растёт сам до «круглого» |
| `get_voice_windows(from_s, to_s)` | секунды с речью: доля речи и вероятности эмоций голоса |
| `detect_moments()` | моменты профиля от самого значимого, с кандидатами и метриками |
| `find_sustained(emotion, min_prob, min_duration_s)` | устойчивая эмоция со своими порогами |
| `find_abrupt_changes(min_jsd, min_hold_s)` | резкие смены со своими порогами |
| `get_moment_context(start_s, end_s, pad_s)` | до / во время / после: ведущие эмоции, голос, лицо, трекер, речь, другие люди |

- **Формат ответа.** У каждого ответа есть шапка: что это, единицы, шаг. Числа — с двумя знаками, пустая
  ячейка — нет данных. Описание ручки (его видит модель) — docstring метода.
- **Ошибки параметров** (пустой интервал, неизвестная эмоция или модальность) — это `ToolError`. Модель
  получает текст ошибки с признаком `is_error` и может исправить вызов.

## Профили приватности

| | `external` | `local` |
|---|---|---|
| человек | `P1` | плюс id из базы, если узнан |
| время | секунды от начала сессии | плюс имя сессии, id и время начала |

- Набор ручек в первой версии одинаков в обоих профилях. Ручки истории человека и кадров записи появятся во
  второй версии и только в `local`: в `external` их просто не будет в списке.
- Тест `test_overview_hides_identity_in_external` проверяет, что в `external` нет имени сессии, её id, id
  человека и даты.

## Права только на чтение

| Где | Как |
|---|---|
| S3 | ключ `reader` (`S3_READER_ACCESS_KEY` / `S3_READER_SECRET_KEY`): `Read` и `List` только бакета записей |
| PostgreSQL | роль `affectra_reader`: только представления схемы `reader`, по умолчанию read-only транзакции |

Ключи SeaweedFS задаёт `deploy/seaweedfs/s3-start.sh`, роль и представление `reader.sessions` — миграция
`004_reader_views`. Вход для роли включает веб-сервер при старте, если задан `AFFECTRA_DB_READER_PASSWORD`.

Проверено на стеке:
- **reader** — читает записи; запись, удаление и чужой бакет запрещены;
- **agent** — пишет и читает только свой бакет `affectra-agent-reports`, записи системы ему недоступны;
- **`affectra_reader`** — видит `reader.sessions`, а к таблицам, на изменение и на создание таблиц получает
  отказ.

## Настройки

| Переменная | Смысл |
|---|---|
| `S3_ENDPOINT`, `S3_BUCKET` | хранилище записей |
| `S3_READER_ACCESS_KEY`, `S3_READER_SECRET_KEY` | ключ только на чтение |
| `DB_HOST`, `DB_PORT`, `DB_NAME` | PostgreSQL |
| `DB_READER_USER`, `DB_READER_PASSWORD` | роль `affectra_reader`; без пароля метаданные сессии не читаются |
| `AGENT_PROFILES_DIR` | каталог профилей сценариев |
