import { useState } from 'react';
import { MOCK_CAMERAS } from '@/data/mock';
import Toggle from '@/components/Toggle';

const TRACKER_LABEL = { deepsort: 'DeepSORT', bytetrack: 'ByteTrack' } as const;
const MODEL_LABEL: Record<string, string> = {
  'resnet-18': 'ResNet-18',
  'resnet-18-int8': 'ResNet-18 INT8',
  'resnet-50': 'ResNet-50',
  convnext: 'ConvNeXt',
};

export default function SettingsPage() {
  const [device, setDevice] = useState<'cpu' | 'cuda'>('cpu');

  return (
    <>
      <div className="mb-6">
        <h1 className="text-3xl font-extrabold tracking-tight">Настройки</h1>
        <p className="text-slate-600">
          Подключения к камерам и параметры обработки по умолчанию.
        </p>
      </div>

      <div className="grid md:grid-cols-3 gap-6">
        <section className="glass p-6 md:col-span-2">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-bold">Камеры</h3>
            <button className="btn btn-primary text-sm">
              <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              Добавить камеру
            </button>
          </div>
          <div className="divide-y divide-indigo-100/60">
            {MOCK_CAMERAS.map((c) => (
              <div key={c.id} className="py-3 flex items-center gap-4 flex-wrap">
                <span
                  className="pulse-dot"
                  style={{ background: c.status === 'live' ? '#22d3ee' : '#a78bfa' }}
                />
                <div className="flex-1 min-w-[200px]">
                  <div className="font-medium">
                    {c.id} · {c.name}
                  </div>
                  <div className="text-xs text-slate-500 font-mono">
                    {c.url} · {c.resolution}
                  </div>
                </div>
                <span className="chip chip-on">{TRACKER_LABEL[c.tracker]}</span>
                <span className="chip">{MODEL_LABEL[c.model]}</span>
                <span
                  className={
                    c.status === 'live' ? 'chip chip-ok' : c.status === 'slow' ? 'chip chip-warn' : 'chip'
                  }
                >
                  {c.status === 'live' ? 'в эфире' : c.status === 'slow' ? 'низкий FPS' : 'офлайн'}
                </span>
                <button className="btn btn-ghost text-sm">Изменить</button>
              </div>
            ))}
          </div>

          <h4 className="font-semibold mt-8 mb-3">Добавить новую камеру</h4>
          <div className="grid md:grid-cols-2 gap-4">
            <Field label="Название" placeholder="например, «Лобби»" />
            <Field label="Источник" placeholder="rtsp://… или 0 / 1 (индекс устройства)" />
            <SelectField
              label="Трекер"
              options={[
                { value: 'deepsort', label: 'DeepSORT' },
                { value: 'bytetrack', label: 'ByteTrack' },
              ]}
            />
            <SelectField
              label="Модель эмоций"
              options={[
                { value: 'resnet-18', label: 'ResNet-18 (FP32)' },
                { value: 'resnet-18-int8', label: 'ResNet-18 (INT8)' },
                { value: 'convnext', label: 'ConvNeXt' },
              ]}
            />
            <Field label="Частота кадров" type="number" defaultValue={20} />
            <div>
              <div className="label mb-1">Устройство вычислений</div>
              <div className="seg w-full">
                <button
                  className={`flex-1 ${device === 'cpu' ? 'active' : ''}`}
                  onClick={() => setDevice('cpu')}
                >
                  CPU
                </button>
                <button
                  className={`flex-1 ${device === 'cuda' ? 'active' : ''}`}
                  onClick={() => setDevice('cuda')}
                >
                  GPU
                </button>
              </div>
            </div>
          </div>
          <div className="mt-4 flex gap-2">
            <button className="btn btn-primary">Сохранить и подключить</button>
            <button className="btn btn-ghost">Проверить подключение</button>
          </div>
        </section>

        <aside className="glass p-6">
          <h3 className="font-bold mb-4">Глобальные значения</h3>
          <div className="space-y-4">
            <SelectField
              label="Трекер по умолчанию"
              defaultValue="bytetrack"
              options={[
                { value: 'deepsort', label: 'DeepSORT' },
                { value: 'bytetrack', label: 'ByteTrack' },
              ]}
            />
            <SelectField
              label="Модель эмоций по умолчанию"
              defaultValue="resnet-18-int8"
              options={[
                { value: 'resnet-18', label: 'ResNet-18 (FP32)' },
                { value: 'resnet-18-int8', label: 'ResNet-18 (INT8)' },
                { value: 'convnext', label: 'ConvNeXt' },
              ]}
            />
            <div>
              <div className="label mb-1">Устройство вычислений</div>
              <div className="seg w-full">
                <button className="active flex-1">CPU</button>
                <button className="flex-1">GPU</button>
              </div>
            </div>
            <hr className="border-indigo-100/60" />
            <ToggleRow title="Сохранять аннотированный поток" subtitle="На диск, для каждой сессии" defaultOn />
            <ToggleRow title="Идентификация между камерами" subtitle="Общая база лиц" defaultOn />
            <ToggleRow title="Скрывать ID лиц" subtitle="Не показывать на скриншотах" />
            <button className="btn btn-primary w-full">Сохранить</button>
          </div>
        </aside>
      </div>
    </>
  );
}

function Field({
  label,
  placeholder,
  type = 'text',
  defaultValue,
}: {
  label: string;
  placeholder?: string;
  type?: string;
  defaultValue?: string | number;
}) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <input className="field" type={type} placeholder={placeholder} defaultValue={defaultValue} />
    </div>
  );
}

function SelectField({
  label,
  options,
  defaultValue,
}: {
  label: string;
  options: { value: string; label: string }[];
  defaultValue?: string;
}) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <select className="field" defaultValue={defaultValue}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function ToggleRow({
  title,
  subtitle,
  defaultOn = false,
}: {
  title: string;
  subtitle: string;
  defaultOn?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className="font-medium">{title}</div>
        <div className="text-xs text-slate-500">{subtitle}</div>
      </div>
      <Toggle defaultOn={defaultOn} />
    </div>
  );
}
