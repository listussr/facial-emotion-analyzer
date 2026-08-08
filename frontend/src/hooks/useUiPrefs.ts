import { useEffect, useState } from 'react';

/**
 * Локальные UI-настройки, которые не нужно тащить на бэкенд (всё лежит в
 * localStorage). Сейчас тут одна настройка — сколько похожих лиц
 * показывать на странице «История» при поиске по фото.
 */
const STORAGE_KEY = 'affectra:ui-prefs:v1';

export interface UiPrefs {
  searchTopK: number;
}

const DEFAULT: UiPrefs = {
  searchTopK: 5,
};

function load(): UiPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT;
    return { ...DEFAULT, ...JSON.parse(raw) };
  } catch {
    return DEFAULT;
  }
}

function save(p: UiPrefs) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
  } catch {
    /* ignore */
  }
}

export function useUiPrefs() {
  const [prefs, setPrefs] = useState<UiPrefs>(() => load());

  useEffect(() => {
    save(prefs);
  }, [prefs]);

  const update = (patch: Partial<UiPrefs>) => setPrefs((p) => ({ ...p, ...patch }));
  return { prefs, update };
}
