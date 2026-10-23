export default function AboutPage() {
  return (
    <>
      <div className="mb-8">
        <h1 className="text-3xl font-extrabold tracking-tight">О системе Affectra</h1>
        <p className="text-slate-600 max-w-2xl">
          Исследовательский прототип: распознаёт эмоции людей по лицу и голосу в реальном времени и сохраняет
          итоговую запись с разметкой. Работает на обычном компьютере без видеокарты, видео никуда не отправляется.
        </p>
      </div>

      <section className="grid md:grid-cols-3 gap-6 mb-8">
        <div className="glass p-6 md:col-span-2">
          <h3 className="font-bold text-lg mb-2">Что делает</h3>
          <p className="text-slate-700">
            Система находит лица в кадре, следит за каждым человеком и раз в секунду оценивает его эмоцию по
            последним трём секундам. Параллельно слушает звук: когда кто-то говорит, оценивает эмоцию голоса.
            Две оценки сводятся в одну — она устойчивее, когда лицо плохо видно или вокруг шумно. Знакомых людей
            система узнаёт и ведёт историю их эмоций.
          </p>
          <h4 className="font-semibold mt-5 mb-1">Распознаёт шесть эмоций</h4>
          <div className="flex flex-wrap gap-2">
            {['гнев', 'отвращение', 'страх', 'радость', 'нейтральное', 'грусть'].map((e) => (
              <span key={e} className="chip">{e}</span>
            ))}
          </div>
          <h4 className="font-semibold mt-5 mb-1">Источники</h4>
          <ul className="list-disc list-inside text-slate-700 space-y-1">
            <li>камера и микрофон этого компьютера прямо в браузере;</li>
            <li>видео- и аудиофайлы;</li>
            <li>IP-камеры и потоки (RTSP, HTTP).</li>
          </ul>
        </div>

        <aside className="glass p-6">
          <h3 className="font-bold mb-3">Модели</h3>
          <ul className="space-y-3 text-sm">
            <Stat label="Лицо" value="Swin-T + время" />
            <Stat label="Голос" value="WavLM-Base+" />
            <Stat label="Слияние" value="стекинг" />
            <Stat label="Детектор лиц" value="YuNet" />
            <Stat label="Детектор речи" value="Silero VAD" />
            <Stat label="Трекер" value="ByteTrack · DeepSORT" />
            <Stat label="Узнавание" value="FaceNet + pgvector" />
          </ul>
        </aside>
      </section>

      <section className="glass p-6 mb-8">
        <h3 className="font-bold text-lg mb-4">Как устроена обработка</h3>
        <p className="text-slate-700 mb-5">
          Каждая стадия — отдельный сервис, сервисы обмениваются данными через Kafka. Их можно перезапускать
          по отдельности и разносить по разным компьютерам.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 relative">
          <div
            className="hidden md:block absolute top-12 left-[7%] right-[7%] h-px"
            style={{ background: 'linear-gradient(90deg,#4f46e5,#7c3aed,#22d3ee)' }}
          />
          <Step n={1} title="Приём" body="Кадры и звук источника с отметками времени уходят в Kafka." />
          <Step n={2} title="Лицо" body="Поиск лиц, слежение за людьми, эмоция по окну кадров за 3 секунды." />
          <Step n={3} title="Голос" body="Детектор речи отсекает тишину и музыку, модель оценивает эмоцию речи." />
          <Step n={4} title="Слияние" body="Оценки лица и голоса, совпавшие по времени, сводятся в одну." />
          <Step n={5} title="Итог" body="Видео с рамками и звуком и вся аналитика сохраняются как запись сессии." />
        </div>
      </section>

      <section className="grid md:grid-cols-2 gap-6 mb-8">
        <div className="glass p-6">
          <h3 className="font-bold text-lg mb-3">Точность</h3>
          <p className="text-sm text-slate-700 mb-3">
            Модели обучены на корпусе CREMA-D и проверены на актёрах, которых не было в обучении. UAR — средняя
            по эмоциям доля верно распознанных примеров; случайное угадывание дало бы 0.17.
          </p>
          <ul className="space-y-2 text-sm">
            <Stat label="Только лицо" value="0.69" />
            <Stat label="Только голос" value="0.76" />
            <Stat label="Лицо + голос" value="0.84" />
          </ul>
        </div>
        <div className="glass p-6">
          <h3 className="font-bold text-lg mb-3">Скорость</h3>
          <p className="text-sm text-slate-700 mb-3">
            Ноутбук с Intel Core i5-1135G7, только процессор, режим «Быстро» (int8-модели).
          </p>
          <ul className="space-y-2 text-sm">
            <Stat label="Видео" value="20 кадров/с" />
            <Stat label="Задержка анализа" value="40–110 мс" />
            <Stat label="Голос на окно 3 с" value="~0.1 с" />
          </ul>
        </div>
      </section>

      <section className="glass p-6">
        <h3 className="font-bold text-lg mb-3">Ограничения и этика</h3>
        <ul className="list-disc list-inside text-slate-700 space-y-2">
          <li>
            Модели учились на постановочной актёрской речи на английском. На живых разговорах, других языках
            и при слабом освещении точность ниже.
          </li>
          <li>
            Система угадывает выраженную эмоцию, а не то, что человек чувствует на самом деле. Считайте результат
            <strong> сигналом, а не истиной</strong>, и не принимайте на его основе решений о конкретных людях.
          </li>
          <li>
            Анализируйте людей только с их согласия. Все данные — записи, история эмоций, снимки лиц — хранятся
            локально, на этом компьютере или в вашей сети. Записи удаляются в разделе «Записи».
          </li>
        </ul>
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <li className="flex justify-between gap-3">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono text-right">{value}</span>
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
