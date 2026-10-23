import { useEffect, useState } from 'react';
import { browserCapture, CaptureState } from './browserCapture';

export function useBrowserCapture(): CaptureState {
  const [state, setState] = useState<CaptureState>(browserCapture.current);
  useEffect(() => browserCapture.subscribe(setState), []);
  return state;
}
