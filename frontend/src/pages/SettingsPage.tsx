import { useState } from 'react';
import { useSessions } from '@/hooks/useSessions';
import { api } from '@/api/client';
import Toggle from '@/components/Toggle';
import type { TrackerName, EmotionModel, Device } from '@/types';

const TRACKER_LABEL: Record<TrackerName, string> = { deepsort: 'DeepSORT', bytetrack: 'ByteTrack' };
const MODEL_LABEL: Record<string, string> = {
  'resnet-18': 'ResNet-18',
  'resnet-18-int8': 'ResNet-18 INT8',
  'resnet-50': 'ResNet-50',
  convnext: 'ConvNeXt',
};

export default function SettingsPage() {
  const { sessions, error: listError, stop, refresh } = useSessions('camera');

  const [name, setName] = useState('');
  const [source, setSource] = useState('');
  const [frameRate, setFrameRate] = useState(20);
  const [tracker, setTracker] = useState<TrackerName>('deepsort');
  const [model, setModel] = useState<EmotionModel>('resnet-18');
  const [device, setDevice] = useState<Device>('cpu');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function addCamera() {
    if (!name.trim() || !source.trim()) {
      setError('Укажите имя и источник');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createCamera({
        name: name.trim(),
        source: source.trim(),
        frame_rate: frameRate,
        config: { tracker, model, device },
      });
      setName('');
      setSource('');
      await refresh();
    } catch (e: any) {
      setError(e?.message || 'Не удалось создать камеру');
    } finally {
      setBusy(false);
    }
  }

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
            <span className="text-xs text-slate-500">всего: {sessions.length}</span>
          </div>

          {listError && <div className="text-sm text-rose-700 mb-3">{listError}</div>}

          <div className="divide-y divide-indigo-100/60">
            {sessions.length === 0 && (
              <p className="text-sm text-slate-500 py-3">
                Нет подключённых камер. Добавьте первую в форме ниже.
              </p>
            )}
            {sessions.map((c) => {
              if (c.kind !== 'camera') return null;
              return (
                <div key={c.id} className="py-3 flex items-center gap-4 flex-wrap">
                  <span
                    className="pulse-dot"
                    style={{ background: c.status === 'live' ? '#22d3ee' : '#a78bfa' }}
                  />
                  <div className="flex-1 min-w-[200px]">
                    <div className="font-medium">
                      {c.id} · {c.name}
                    </div>
                    <div className="text-xs text-slate-500 font-mono break-all">{c.url}</div>
                  </div>
                  <span className="chip chip-on">{TRACKER_LABEL[c.tracker]}</span>
                  <span className="chip">{MODEL_LABEL[c.model] || c.model}</span>
                  <span
                    className={
                      c.status === 'live'
                        ? 'chip chip-ok'
                        : c.status === 'slow' || c.status === 'error'
                        ? 'chip chip-warn'
                        : 'chip'
                    }
                  >
                    {c.status === 'live'
                      ? 'в эфире'
                      : c.status === 'slow'
                      ? 'низкий FPS'
                      : c.status === 'error'
                      ? 'ошибка'
                      : 'офлайн'}
                  </span>
                  <button className="btn btn-ghost text-sm" onClick={() => stop(c.id)}>
                    Остановить
                  </button>
                </div>
              );
            })}
          </div>

          <h4 className="font-semibold mt-8 mb-3">Добавить новую камеру</h4>
          {error && <div className="text-sm text-rose-700 mb-3">{error}</div>}
          <div className="grid md:grid-cols-2 gap-4">
            <Field label="Название" value={name} onChange={setName} placeholder="например, «Лобби»" />
            <Field
              label="Источник"
              value={source}
              onChange={setSource}
              placeholder="rtsp://… или 0 / путь к файлу"
            />
            <SelectField
              label="Трекер"
              value={tracker}
              onChange={(v) => setTracker(v as TrackerName)}
              options={[
                { value: 'deepsort', label: 'DeepSORT' },
                { value: 'bytetrack', label: 'ByteTrack' },
              ]}
            />
            <SelectField
              label="Модель эмоций"
              value={model}
              onChange={(v) => setModel(v as EmotionModel)}
              options={[
                { value: 'resnet-18', label: 'ResNet-18 (FP32)' },
                { value: 'resnet-18-int8', label: 'ResNet-18 (INT8)' },
                { value: 'convnext', label: 'ConvNeXt' },
              ]}
            />
            <div>
              <div className="label mb-1">Частота кадров</div>
              <input
                className="field"
                type="number"
                value={frameRate}
                onChange={(e) => setFrameRate(Number(e.target.value) || 20)}
              />
            </div>
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
            <button
              className="btn btn-primary disabled:opacity-50"
              onClick={addCamera}
              disabled={busy}
            >
              {busy ? 'Подключение…' : 'Сохранить и подключить'}
            </button>
          </div>
        </section>

        <aside className="glass p-6">
          <h3 className="font-bold mb-4">Глобальные значения</h3>
          <p className="text-xs text-slate-500 mb-4">
            Сейчас параметры обработки задаются на уровне процесса <code>consumer.py</code>.
            Per-session маршрутизация — следующий этап.
          </p>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium">Сохранять аннотированный поток</div>
                <div className="text-xs text-slate-500">На диск, для каждой сессии</div>
              </div>
              <Toggle defaultOn />
            </div>
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium">Идентификация между камерами</div>
                <div className="text-xs text-slate-500">Общая база лиц</div>
              </div>
              <Toggle defaultOn />
            </div>
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium">Скрывать ID лиц</div>
                <div className="text-xs text-slate-500">Не показывать на скриншотах</div>
              </div>
              <Toggle />
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <input
        className="field"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}
