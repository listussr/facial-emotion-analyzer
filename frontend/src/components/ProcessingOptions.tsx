import Toggle from '@/components/Toggle';
import { CLIP_FRAMES_OPTIONS, FUSION_METHODS, VOICE_MODELS } from '@/data/models';
import type { ClipFrames, FusionMethod, Modality, VoiceModel } from '@/types';

export const MODALITY_LABEL: Record<Modality, string> = { video: 'Видео', audio: 'Звук' };

interface Props {
  modalities: Modality[];
  onModalitiesChange: (m: Modality[]) => void;
  voiceModel: VoiceModel;
  onVoiceModelChange: (m: VoiceModel) => void;
  identification: boolean;
  onIdentificationChange: (on: boolean) => void;
  clipFrames: ClipFrames;
  onClipFramesChange: (n: ClipFrames) => void;
  fusionMethod: FusionMethod;
  onFusionMethodChange: (m: FusionMethod) => void;
  /** Подсказка о том, что есть в выбранном источнике (для загруженного файла) */
  sourceHas?: { video?: boolean | null; audio?: boolean | null };
}

/**
 * Общие параметры обработки сессии: модальности, модель голоса, кадры на окно
 * модели лица, метод слияния, идентификация.
 * Используются при создании камеры и при обработке загруженного файла.
 */
export default function ProcessingOptions({
  modalities,
  onModalitiesChange,
  voiceModel,
  onVoiceModelChange,
  identification,
  onIdentificationChange,
  clipFrames,
  onClipFramesChange,
  fusionMethod,
  onFusionMethodChange,
  sourceHas,
}: Props) {
  const fusionHint = FUSION_METHODS.find((m) => m.value === fusionMethod)?.hint;
  const framesHint = CLIP_FRAMES_OPTIONS.find((o) => o.value === clipFrames)?.hint;
  const toggle = (m: Modality) => {
    const next = modalities.includes(m) ? modalities.filter((x) => x !== m) : [...modalities, m];
    if (next.length > 0) onModalitiesChange(next);    // хотя бы одна модальность
  };
  const missing = (m: Modality) => sourceHas && sourceHas[m] === false && modalities.includes(m);

  return (
    <div className="space-y-4">
      <div>
        <div className="label mb-1">Модальности</div>
        <div className="seg w-full">
          {(['video', 'audio'] as Modality[]).map((m) => (
            <button
              key={m}
              type="button"
              className={`flex-1 ${modalities.includes(m) ? 'active' : ''}`}
              onClick={() => toggle(m)}
              title={modalities.length === 1 && modalities.includes(m) ? 'Нужна хотя бы одна модальность' : ''}
            >
              {MODALITY_LABEL[m]}
            </button>
          ))}
        </div>
        {(['video', 'audio'] as Modality[]).filter(missing).map((m) => (
          <div key={m} className="text-xs text-amber-700 mt-1">
            В файле нет дорожки «{MODALITY_LABEL[m].toLowerCase()}» — обработка пойдёт без неё.
          </div>
        ))}
      </div>

      {modalities.includes('audio') && (
        <div>
          <div className="label mb-1">Модель эмоций голоса</div>
          <select
            className="field"
            value={voiceModel}
            onChange={(e) => onVoiceModelChange(e.target.value)}
          >
            {VOICE_MODELS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {modalities.includes('video') && (
        <div>
          <div className="label mb-1">Кадров на окно (лицо, окно 3 с)</div>
          <div className="seg w-full">
            {CLIP_FRAMES_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                className={`flex-1 ${clipFrames === o.value ? 'active' : ''}`}
                onClick={() => onClipFramesChange(o.value)}
                title={o.hint}
              >
                {o.label}
              </button>
            ))}
          </div>
          {framesHint && <div className="text-xs text-slate-500 mt-1">{framesHint}</div>}
        </div>
      )}

      {modalities.includes('video') && modalities.includes('audio') && (
        <div>
          <div className="label mb-1">Слияние лица и голоса</div>
          <select
            className="field"
            value={fusionMethod}
            onChange={(e) => onFusionMethodChange(e.target.value as FusionMethod)}
          >
            {FUSION_METHODS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
          {fusionHint && <div className="text-xs text-slate-500 mt-1">{fusionHint}</div>}
        </div>
      )}

      {modalities.includes('video') && (
        <div className="flex items-center justify-between">
          <div>
            <div className="font-medium">Идентифицировать лица</div>
            <div className="text-xs text-slate-500">Сопоставлять с базой и сохранять историю</div>
          </div>
          <Toggle defaultOn={identification} onChange={onIdentificationChange} />
        </div>
      )}
    </div>
  );
}
