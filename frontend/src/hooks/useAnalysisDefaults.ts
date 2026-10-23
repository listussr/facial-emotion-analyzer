import { useCallback, useEffect, useState } from 'react';
import type { SessionConfigDTO } from '@/api/client';
import {
  DEFAULT_CLIP_FRAMES, DEFAULT_EMOTION_MODEL, DEFAULT_FUSION_METHOD, DEFAULT_VOICE_MODEL,
} from '@/data/models';
import type { ClipFrames, Device, EmotionModel, FusionMethod, Modality, TrackerName, VoiceModel } from '@/types';

const STORAGE_KEY = 'affectra:analysis-defaults:v1';
const EVENT = 'affectra:analysis-defaults';

/** Параметры анализа, которые подставляются в каждую новую сессию; меняются на странице «Настройки». */
export interface AnalysisDefaults {
  frameRate: number;
  model: EmotionModel;
  voiceModel: VoiceModel;
  clipFrames: ClipFrames;
  fusionMethod: FusionMethod;
  identification: boolean;
  tracker: TrackerName;
  device: Device;
}

export const RECOMMENDED: AnalysisDefaults = {
  frameRate: 20,
  model: DEFAULT_EMOTION_MODEL,
  voiceModel: DEFAULT_VOICE_MODEL,
  clipFrames: DEFAULT_CLIP_FRAMES,
  fusionMethod: DEFAULT_FUSION_METHOD,
  identification: true,
  tracker: 'bytetrack',
  device: 'cpu',
};

function load(): AnalysisDefaults {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? { ...RECOMMENDED, ...JSON.parse(raw) } : RECOMMENDED;
  } catch {
    return RECOMMENDED;
  }
}

export function isRecommended(d: AnalysisDefaults): boolean {
  return (Object.keys(RECOMMENDED) as (keyof AnalysisDefaults)[]).every((k) => d[k] === RECOMMENDED[k]);
}

export function toSessionConfig(d: AnalysisDefaults, modalities: Modality[]): SessionConfigDTO {
  return {
    tracker: d.tracker,
    model: d.model,
    device: d.device,
    modalities,
    voice_model: d.voiceModel,
    identification: d.identification,
    clip_frames: d.clipFrames,
    fusion_method: d.fusionMethod,
  };
}

export function useAnalysisDefaults() {
  const [defaults, setDefaults] = useState<AnalysisDefaults>(load);

  useEffect(() => {
    const sync = () => setDefaults(load());
    window.addEventListener(EVENT, sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener('storage', sync);
    };
  }, []);

  const save = useCallback((next: AnalysisDefaults) => {
    setDefaults(next);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      return;
    }
    window.dispatchEvent(new Event(EVENT));
  }, []);

  const update = useCallback((patch: Partial<AnalysisDefaults>) => save({ ...defaults, ...patch }), [save, defaults]);
  const reset = useCallback(() => save(RECOMMENDED), [save]);

  return { defaults, update, reset };
}
