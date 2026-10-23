import { ReactNode, useState } from 'react';
import { Link } from 'react-router-dom';
import Toggle from '@/components/Toggle';
import { useUiPrefs } from '@/hooks/useUiPrefs';
import { AnalysisDefaults, isRecommended, useAnalysisDefaults } from '@/hooks/useAnalysisDefaults';
import { CLIP_FRAMES_OPTIONS, EMOTION_MODELS, FUSION_METHODS, VOICE_MODELS } from '@/data/models';
import type { ClipFrames, Device, EmotionModel, FusionMethod, TrackerName, VoiceModel } from '@/types';

const MODES: { value: string; label: string; hint: string; preset: Partial<AnalysisDefaults> }[] = [
  {
    value: 'fast',
    label: 'Быстро',
    hint: 'Подходит для живой камеры на обычном компьютере.',
    preset: { model: 'crema-swin-int8', voiceModel: 'crema-wavlm-int8', clipFrames: 8 },
  },
  {
    value: 'accurate',
    label: 'Точнее',
    hint: 'Немного точнее, но нагрузка на процессор примерно вдвое выше.',
    preset: { model: 'crema-swin', voiceModel: 'crema-wavlm', clipFrames: 16 },
  },
];

const FRAME_RATES = [10, 15, 20, 30];

function modeOf(d: AnalysisDefaults): string | null {
  const m = MODES.find((x) => Object.entries(x.preset).every(([k, v]) => d[k as keyof AnalysisDefaults] === v));
  return m?.value ?? null;
}

export default function SettingsPage() {
  const { defaults: d, update, reset } = useAnalysisDefaults();
  const { prefs, update: updatePrefs } = useUiPrefs();
  const mode = modeOf(d);
  const fusion = FUSION_METHODS.find((m) => m.value === d.fusionMethod);

  return (
    <>
      <div className="mb-6">
        <h1 className="text-3xl font-extrabold tracking-tight">Настройки</h1>
        <p className="text-slate-600 mt-1">
          Эти параметры используются для каждого нового анализа. Изменения сохраняются сразу.
        </p>
      </div>

      <section className="glass p-6 mb-6 max-w-3xl">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-2">
          <h2 className="text-lg font-bold">Анализ</h2>
          <button type="button" className="btn btn-ghost text-sm py-1.5" disabled={isRecommended(d)} onClick={reset}>
            Вернуть рекомендуемые
          </button>
        </div>

        <Row title="Режим" hint={MODES.find((m) => m.value === mode)?.hint ?? 'Свои модели — см. раздел «Для специалистов».'}>
          <div className="seg">
            {MODES.map((m) => (
              <button key={m.value} type="button" className={mode === m.value ? 'active' : ''} onClick={() => update(m.preset)}>
                {m.label}
              </button>
            ))}
          </div>
        </Row>

        <Row title="Частота анализа" hint="Сколько кадров в секунду разбирать. Больше — плавнее, но выше нагрузка.">
          <div className="seg">
            {FRAME_RATES.map((f) => (
              <button key={f} type="button" className={d.frameRate === f ? 'active' : ''} onClick={() => update({ frameRate: f })}>
                {f}
              </button>
            ))}
          </div>
        </Row>

        <Row title="Узнавать людей" hint="Запоминать лица, чтобы собирать историю эмоций человека между сессиями.">
          <Toggle on={d.identification} onChange={(v) => update({ identification: v })} label="Узнавать людей" />
        </Row>

        <Row title="Как объединять лицо и голос" hint={fusion?.hint}>
          <select
            className="field sm:w-80"
            value={d.fusionMethod}
            onChange={(e) => update({ fusionMethod: e.target.value as FusionMethod })}
          >
            {FUSION_METHODS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </Row>

        <Expert>
          <div className="grid sm:grid-cols-2 gap-4">
            <Select
              label="Модель лица"
              value={d.model}
              options={EMOTION_MODELS}
              onChange={(v) => update({ model: v as EmotionModel })}
            />
            <Select
              label="Модель голоса"
              value={d.voiceModel}
              options={VOICE_MODELS}
              onChange={(v) => update({ voiceModel: v as VoiceModel })}
            />
            <Select
              label="Кадров на окно лица (3 с)"
              value={String(d.clipFrames)}
              options={CLIP_FRAMES_OPTIONS.map((o) => ({ value: String(o.value), label: `${o.label} — ${o.hint}` }))}
              onChange={(v) => update({ clipFrames: Number(v) as ClipFrames })}
            />
            <Select
              label="Трекер лиц"
              value={d.tracker}
              options={[
                { value: 'bytetrack', label: 'ByteTrack (рекомендуется)' },
                { value: 'deepsort', label: 'DeepSORT' },
              ]}
              onChange={(v) => update({ tracker: v as TrackerName })}
            />
            <Select
              label="Вычисления"
              value={d.device}
              options={[
                { value: 'cpu', label: 'Процессор (CPU)' },
                { value: 'cuda', label: 'Видеокарта (GPU, если есть)' },
              ]}
              onChange={(v) => update({ device: v as Device })}
            />
          </div>
        </Expert>
      </section>

      <section className="glass p-6 mb-6 max-w-3xl">
        <h2 className="text-lg font-bold mb-2">Поиск человека по фото</h2>
        <Row title="Сколько похожих показывать" hint="На странице «Люди».">
          <input
            className="field w-24"
            type="number"
            min={1}
            max={50}
            value={prefs.searchTopK}
            onChange={(e) => updatePrefs({ searchTopK: Math.min(50, Math.max(1, Number(e.target.value) || 1)) })}
          />
        </Row>
      </section>

      <p className="hint">
        Как устроена система — на странице{' '}
        <Link to="/about" className="text-indigo-700 font-medium">
          «О системе»
        </Link>
        .
      </p>
    </>
  );
}

function Row({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-6 py-4 border-b border-slate-100 flex-wrap">
      <div className="min-w-[200px] flex-1">
        <div className="label">{title}</div>
        {hint && <div className="hint mt-0.5 max-w-sm">{hint}</div>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Expert({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="pt-4">
      <button type="button" className="text-sm font-medium text-slate-600 hover:text-slate-900" onClick={() => setOpen((v) => !v)}>
        {open ? '▾' : '▸'} Для специалистов
      </button>
      {open && <div className="mt-4">{children}</div>}
    </div>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <select className="field mt-1" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
