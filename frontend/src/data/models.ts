import type { EmotionModel } from '@/types';

/**
 * Единый список моделей для распознавания эмоций.
 * Должен соответствовать `_models_pathes` в emotion_recognizer.py.
 *
 * - `value`        — строка, которая уходит на бэкенд.
 * - `label`        — что показывает выпадашка.
 * - `chip`         — короткая подпись на чипе тайла/фокуса.
 */
export interface ModelOption {
  value: EmotionModel;
  label: string;
  chip: string;
}

export const EMOTION_MODELS: ModelOption[] = [
  { value: 'resnet-18',           label: 'ResNet-18 (FP32)',         chip: 'ResNet-18' },
  { value: 'resnet-18-int8',      label: 'ResNet-18 (INT8)',         chip: 'ResNet-18 INT8' },
  { value: 'resnet-50',           label: 'ResNet-50 (FP32)',         chip: 'ResNet-50' },
  { value: 'resnet-50-int8',      label: 'ResNet-50 (INT8)',         chip: 'ResNet-50 INT8' },
  { value: 'convnext',            label: 'ConvNeXt — basic (FP32)',  chip: 'ConvNeXt' },
  { value: 'convnext-int8',       label: 'ConvNeXt — basic (INT8)',  chip: 'ConvNeXt INT8' },
  { value: 'convnext-gelu',       label: 'ConvNeXt — GELU head',     chip: 'ConvNeXt GELU' },
  { value: 'efficientnet-b3',     label: 'EfficientNet-B3 (FP32)',   chip: 'EfficientNet-B3' },
  { value: 'efficientnet-b3-int8', label: 'EfficientNet-B3 (INT8)',  chip: 'EfficientNet-B3 INT8' },
  { value: 'swin-tiny',           label: 'Swin Tiny (FP32)',         chip: 'Swin Tiny' },
  { value: 'swin-tiny-int8',      label: 'Swin Tiny (INT8)',         chip: 'Swin Tiny INT8' },
];

export const MODEL_LABEL: Record<string, string> = Object.fromEntries(
  EMOTION_MODELS.map((m) => [m.value, m.label])
);

export const MODEL_CHIP: Record<string, string> = Object.fromEntries(
  EMOTION_MODELS.map((m) => [m.value, m.chip])
);
