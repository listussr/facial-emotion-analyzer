# Affectra — frontend

React + Vite + TypeScript + Tailwind CSS.

## Запуск

```powershell
cd frontend
npm install
npm run dev
```

Откроется на http://localhost:5173

Запросы к `/api/*` проксируются на http://localhost:8000 (будущий FastAPI).

## Сборка

```powershell
npm run build
npm run preview
```

## Структура

```
src/
  components/   — переиспользуемые UI-компоненты
    Header, Layout
    StreamTile      — один видеотайл (заглушка → MJPEG)
    StreamGrid      — сетка тайлов 1×1 / 2×2 / 3×3
    GridSelector    — переключатель плотности сетки
    Segmented, Toggle
  pages/
    HomePage        — главная с навигацией
    CamerasPage     — сетка живых камер
    UploadsPage     — загрузка + сетка обрабатываемых файлов
    StreamFocusPage — развёрнутый просмотр одного источника
    SettingsPage    — список и добавление камер, глобальные дефолты
    AboutPage       — описание системы
  data/mock.ts      — данные-заглушки (заменим на /api после backend)
  types/index.ts    — общие типы и словари (эмоции на русском)
  router.tsx        — routes (camera/:id и upload/:id отдельно)
```

## Что заглушено

- В `StreamTile` сейчас красивый плейсхолдер с боксами. Когда поднимем
  FastAPI и MJPEG-эндпоинт, замените блок `.live-tile` на
  `<img src={`/api/stream/${source.id}`} />`.
- `MOCK_CAMERAS` / `MOCK_UPLOADS` → запросы к `/api/sessions`, `/api/uploads`.
- Drag-and-drop в `UploadsPage` сейчас только открывает диалог выбора файла.
  POST на `/api/uploads` подключим вместе с backend.
