export default function AboutPage() {
  return (
    <>
      <div className="mb-8">
        <h1 className="text-3xl font-extrabold tracking-tight">О системе Affectra</h1>
        <p className="text-slate-600 max-w-2xl">
          Исследовательский прототип для анализа эмоций по лицу в реальном времени, работающий
          на локальном железе. Создан в рамках НИР — мы честны насчёт компромиссов.
        </p>
      </div>

      <section className="grid md:grid-cols-3 gap-6 mb-8">
        <div className="glass p-6 md:col-span-2">
          <h3 className="font-bold text-lg mb-2">Назначение</h3>
          <p className="text-slate-700">
            Affectra принимает один или несколько видеопотоков и формирует непрерывную
            покадровую ленту эмоций для каждого треккируемого лица. Система предназначена для
            аналитиков, исследователей и HCI-экспериментов, где нужны и агрегированные показатели
            (настроение группы), и индивидуальная динамика (один человек во времени) — без отправки
            видео во внешние сервисы.
          </p>
          <h4 className="font-semibold mt-5 mb-1">Подходит для</h4>
          <ul className="list-disc list-inside text-slate-700 space-y-1">
            <li>Аналитика лекций и совещаний</li>
            <li>UX-исследования по записанным интервью</li>
            <li>Мониторинг настроения у ресепшна или очереди</li>
          </ul>
          <h4 className="font-semibold mt-5 mb-1">Не подходит для</h4>
          <ul className="list-disc list-inside text-slate-700 space-y-1">
            <li>Идентификации незнакомых людей без их согласия</li>
            <li>Слежки и решений правоохранительных органов</li>
            <li>Production-grade аффективного анализа</li>
          </ul>
        </div>

        <aside className="glass p-6">
          <h3 className="font-bold mb-3">Кратко о стеке</h3>
          <ul className="space-y-3 text-sm">
            <Stat label="Архитектура" value="Kafka pipeline" />
            <Stat label="Детектор" value="MediaPipe Face" />
            <Stat label="Трекеры" value="DeepSORT · ByteTrack" />
            <Stat label="Эмоции" value="ResNet-18 / ConvNeXt" />
            <Stat label="Идентификация" value="FaceNet + pgvector" />
            <Stat label="Производительность" value="~25 FPS · i5-1135G7" />
          </ul>
        </aside>
      </section>

      <section className="glass p-6 mb-8">
        <h3 className="font-bold text-lg mb-4">Конвейер обработки</h3>
        <p className="text-slate-700 mb-5">
          Каждый кадр проходит пять стадий через Kafka. Стадии слабо связаны — алгоритмы
          можно менять без правки остального кода.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 relative">
          <div
            className="hidden md:block absolute top-12 left-[7%] right-[7%] h-px"
            style={{ background: 'linear-gradient(90deg,#4f46e5,#7c3aed,#22d3ee)' }}
          />
          <Step n={1} title="Захват" body="RTSP / веб-камера / файл → JPEG в Kafka raw-video-frames." />
          <Step n={2} title="Детекция" body="MediaPipe ищет лица на уменьшенном кадре; геометрические фильтры отсекают шум." />
          <Step n={3} title="Трекинг" body="DeepSORT (Re-ID) или ByteTrack (motion) присваивает стабильный ID каждому лицу." />
          <Step n={4} title="Идентификация" body="Эмбеддинг FaceNet + поиск в pgvector; новые лица добавляются асинхронно." />
          <Step n={5} title="Эмоции" body="Батч-инференс ONNX/Torch → 8 классов эмоций со сглаживанием EMA." />
        </div>
      </section>

      <section className="grid md:grid-cols-2 gap-6 mb-8">
        <div className="glass p-6">
          <h3 className="font-bold text-lg mb-3">Трекеры</h3>
          <div className="space-y-4">
            <CompareItem
              tag="DeepSORT"
              note="точность"
              text="Калман + Re-ID по внешности. Лучше держит ID при перекрытиях, но медленнее из-за нейронных эмбеддингов."
            />
            <CompareItem
              tag="ByteTrack"
              note="скорость"
              text="Только movement-association, без нейросети. На CPU в ~10× быстрее, но больше ID-switch при толпе."
            />
          </div>
        </div>
        <div className="glass p-6">
          <h3 className="font-bold text-lg mb-3">Модели эмоций</h3>
          <div className="space-y-4">
            <CompareItem
              tag="ResNet-18"
              note="баланс"
              text="Обучена на AffectNet. FP32 — на точность, INT8 — на скорость, где CPU поддерживает квантизацию."
            />
            <CompareItem
              tag="ConvNeXt"
              note="точность"
              text="Тяжелее, лучше распознаёт тонкие выражения; для real-time нужен GPU."
            />
          </div>
        </div>
      </section>

      <section className="glass p-6">
        <h3 className="font-bold text-lg mb-3">Конфиденциальность и этика</h3>
        <p className="text-slate-700">
          Видео не покидает вашу сеть. Эмбеддинги лиц хранятся в локальном Postgres с pgvector
          и могут быть удалены из настроек. Алгоритмы распознавания эмоций известны своей
          культурной предвзятостью — относитесь к предсказаниям как к <strong>сигналу, а не
          истине в последней инстанции</strong>, и не принимайте на их основе решений в отношении
          конкретных людей.
        </p>
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <li className="flex justify-between">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono">{value}</span>
    </li>
  );
}
function Step({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <div className="glass p-4 relative z-10">
      <span className="step-num mb-2">{n}</span>
      <h4 className="font-bold mt-2">{title}</h4>
      <p className="text-sm text-slate-600">{body}</p>
    </div>
  );
}
function CompareItem({ tag, note, text }: { tag: string; note: string; text: string }) {
  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="chip chip-on">{tag}</span>
        <span className="text-xs text-slate-500">{note}</span>
      </div>
      <p className="text-sm text-slate-700 mt-2">{text}</p>
    </div>
  );
}
