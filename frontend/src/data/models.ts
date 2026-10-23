import type { ClipFrames, EmotionModel, FusionMethod, VoiceModel } from '@/types';

/**
 * Модели распознавания эмоций, доступные в интерфейсе.
 *
 * Система работает с 6 классами CREMA-D (src/emotion_labels.py). Лицо — модель
 * по окну кадров, голос — WavLM, обе из models/crema (int8 — по умолчанию,
 * fp32 — эталон); заглушка
 * `stub` НЕ отражает реальные эмоции. Новая модель добавляется сюда и в реестр бэкенда:
 *   - лицо: `CREMA_MODELS` в src/frames_processing/.../clip_model.py;
 *   - голос: `CREMA_VOICE_MODELS` / `VOICE_MODELS` в src/audio_processing/recognizer.py.
 *
 * - `value`        — строка, которая уходит на бэкенд.
 * - `label`        — что показывает выпадашка.
 * - `chip`         — короткая подпись на чипе тайла/фокуса.
 */
export interface ModelOption<T extends string = string> {
  value: T;
  label: string;
  chip: string;
}

export const EMOTION_MODELS: ModelOption<EmotionModel>[] = [
  { value: 'crema-swin-int8', label: 'CREMA-D Swin-T, int8 (быстрее)', chip: 'Swin int8' },
  { value: 'crema-swin', label: 'CREMA-D Swin-T, fp32 (эталон, медленнее)', chip: 'Swin fp32' },
  { value: 'stub', label: 'Заглушка (6 классов, не реальная модель)', chip: 'stub' },
];

export const VOICE_MODELS: ModelOption<VoiceModel>[] = [
  { value: 'crema-wavlm-int8', label: 'CREMA-D WavLM-Base+, int8 (быстрее)', chip: 'WavLM int8' },
  { value: 'crema-wavlm', label: 'CREMA-D WavLM-Base+, fp32 (эталон, медленнее)', chip: 'WavLM fp32' },
  { value: 'stub', label: 'Заглушка (6 классов, не реальная модель)', chip: 'stub' },
];

/** Алгоритмы слияния лица и голоса (models/crema/usage_rules.md, п. 4.2); подписи — для неспециалиста. */
export const FUSION_METHODS: (ModelOption<FusionMethod> & { hint: string })[] = [
  {
    value: 'stack_hidden',
    label: 'Умное объединение (рекомендуется)',
    chip: 'stack_hidden',
    hint: 'Обученная модель: лучший результат почти всегда, особенно если звук с шумом.',
  },
  {
    value: 'stack_logits',
    label: 'Умное объединение, облегчённое',
    chip: 'stack_logits',
    hint: 'Почти как рекомендуемое, немного хуже при шуме.',
  },
  {
    value: 'weighted_logits',
    label: 'Взвешенное среднее',
    chip: 'weighted',
    hint: 'Простое и прозрачное; хорошо в тихом помещении с чистым звуком.',
  },
  {
    value: 'weighted_logits_robust',
    label: 'Взвешенное среднее для шумного звука',
    chip: 'weighted-robust',
    hint: 'Веса подобраны на зашумлённых записях.',
  },
  {
    value: 'confidence_weighted',
    label: 'Кто увереннее',
    chip: 'confidence',
    hint: 'Больше веса тому, кто уверен сильнее; хорош, когда лицо плохо видно, а звук чистый.',
  },
  { value: 'mean_probs', label: 'Простое среднее', chip: 'mean', hint: 'Базовый способ для сравнения.' },
  { value: 'video_only', label: 'Только по лицу', chip: 'лицо', hint: 'Голос не учитывается.' },
  { value: 'audio_only', label: 'Только по голосу', chip: 'голос', hint: 'Лицо не учитывается.' },
];

export const DEFAULT_FUSION_METHOD: FusionMethod = 'stack_hidden';

export const FUSION_LABEL: Record<string, string> = Object.fromEntries(
  FUSION_METHODS.map((m) => [m.value, m.label])
);

export const CLIP_FRAMES_OPTIONS: { value: ClipFrames; label: string; hint: string }[] = [
  { value: 8, label: '8 кадров', hint: 'Вдвое дешевле, точность ниже на ~0.6 п.п.' },
  { value: 16, label: '16 кадров', hint: 'Как при обучении модели; вдвое больше нагрузка на CPU.' },
];

export const DEFAULT_CLIP_FRAMES: ClipFrames = 8;

export const DEFAULT_EMOTION_MODEL: EmotionModel = EMOTION_MODELS[0].value;
export const DEFAULT_VOICE_MODEL: VoiceModel = VOICE_MODELS[0].value;

export const MODEL_LABEL: Record<string, string> = Object.fromEntries(
  EMOTION_MODELS.map((m) => [m.value, m.label])
);

export const MODEL_CHIP: Record<string, string> = Object.fromEntries(
  EMOTION_MODELS.map((m) => [m.value, m.chip])
);
