import { api, SessionConfigDTO } from '@/api/client';
import type { Modality } from '@/types';

export type CaptureStatus = 'idle' | 'starting' | 'live' | 'error';

export interface CaptureState {
  status: CaptureStatus;
  sessionId: string | null;
  name: string | null;
  error: string | null;
  framesSent: number;
  framesSkipped: number;
  audioSeconds: number;
  stream: MediaStream | null;
}

const KIND_VIDEO = 1;
const KIND_AUDIO = 2;
const AUDIO_RATE = 16000;
const MAX_WIDTH = 640;
const MAX_HEIGHT = 480;
const JPEG_QUALITY = 0.75;
const MAX_BUFFERED = 512 * 1024;

const WORKLET = `
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / ${AUDIO_RATE};
    this.acc = 0; this.sum = 0; this.n = 0;
    this.out = new Int16Array(${AUDIO_RATE / 10}); this.len = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.sum += ch[i]; this.n++; this.acc += 1;
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.sum / this.n));
        this.out[this.len++] = v * 32767;
        this.sum = 0; this.n = 0;
        if (this.len === this.out.length) {
          const buf = this.out.slice(0).buffer;
          this.port.postMessage(buf, [buf]);
          this.len = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('pcm-capture', PcmCapture);
`;

const INITIAL: CaptureState = {
  status: 'idle',
  sessionId: null,
  name: null,
  error: null,
  framesSent: 0,
  framesSkipped: 0,
  audioSeconds: 0,
  stream: null,
};

function packet(kind: number, ptsMs: number, payload: ArrayBuffer): ArrayBuffer {
  const out = new Uint8Array(5 + payload.byteLength);
  const view = new DataView(out.buffer);
  view.setUint8(0, kind);
  view.setUint32(1, Math.max(0, Math.round(ptsMs)) >>> 0);
  out.set(new Uint8Array(payload), 5);
  return out.buffer;
}

/** Камера и микрофон браузера как источник сессии: кадры JPEG и PCM 16 кГц идут по WebSocket. */
class BrowserCapture {
  private state: CaptureState = INITIAL;
  private listeners = new Set<(s: CaptureState) => void>();
  private ws: WebSocket | null = null;
  private audioCtx: AudioContext | null = null;
  private videoTimer: number | undefined;
  private stopping = false;

  get current(): CaptureState {
    return this.state;
  }

  subscribe(fn: (s: CaptureState) => void): () => void {
    this.listeners.add(fn);
    fn(this.state);
    return () => this.listeners.delete(fn);
  }

  private set(patch: Partial<CaptureState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn(this.state));
  }

  async start(opts: { name: string; frameRate: number; config: SessionConfigDTO }): Promise<string> {
    if (this.state.status === 'starting' || this.state.status === 'live') {
      throw new Error('Захват уже идёт');
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      const https = `https://${window.location.hostname}:8443${window.location.pathname}`;
      throw new Error(
        window.isSecureContext
          ? 'Браузер не даёт доступ к камере'
          : `Камера доступна только по защищённому адресу — откройте ${https}`,
      );
    }
    this.stopping = false;
    this.set({ ...INITIAL, status: 'starting', name: opts.name });

    const wantVideo = opts.config.modalities.includes('video');
    const wantAudio = opts.config.modalities.includes('audio');
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: wantVideo ? { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } } : false,
        audio: wantAudio ? { echoCancellation: true, noiseSuppression: false, autoGainControl: false } : false,
      });
    } catch (e: any) {
      this.set({ status: 'error', error: `Нет доступа к камере или микрофону: ${e?.message || e}` });
      throw e;
    }

    const modalities: Modality[] = [];
    if (stream.getVideoTracks().length) modalities.push('video');
    if (stream.getAudioTracks().length) modalities.push('audio');

    try {
      const session = await api.createBrowserSession({
        name: opts.name,
        frame_rate: opts.frameRate,
        config: { ...opts.config, modalities },
      });
      this.set({ sessionId: session.id, stream });
      await this.connect(session.id, stream, opts.frameRate);
      return session.id;
    } catch (e: any) {
      stream.getTracks().forEach((t) => t.stop());
      this.set({ status: 'error', error: e?.message || 'Не удалось начать сессию', stream: null });
      throw e;
    }
  }

  private connect(sessionId: string, stream: MediaStream, frameRate: number): Promise<void> {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/api/capture/${sessionId}`);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    return new Promise((resolve, reject) => {
      ws.onopen = async () => {
        const t0 = performance.now();
        try {
          if (stream.getAudioTracks().length) await this.startAudio(stream, ws, t0);
          if (stream.getVideoTracks().length) this.startVideo(stream, ws, t0, frameRate);
          this.set({ status: 'live' });
          resolve();
        } catch (e) {
          reject(e);
        }
      };
      ws.onerror = () => reject(new Error('Не удалось подключиться к серверу захвата'));
      ws.onclose = (ev) => {
        const unexpected = !this.stopping;
        this.cleanup();
        this.set(
          unexpected
            ? { status: 'error', error: `Соединение закрыто сервером${ev.reason ? `: ${ev.reason}` : ''}` }
            : { status: 'idle' },
        );
      };
    });
  }

  private async startAudio(stream: MediaStream, ws: WebSocket, t0: number) {
    const ctx = new AudioContext();
    this.audioCtx = ctx;
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
    try {
      await ctx.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    const src = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, 'pcm-capture');
    const mute = ctx.createGain();
    mute.gain.value = 0;
    src.connect(node).connect(mute).connect(ctx.destination);

    let firstPts: number | null = null;
    let samples = 0;
    node.port.onmessage = (ev: MessageEvent<ArrayBuffer>) => {
      const n = ev.data.byteLength / 2;
      if (firstPts === null) firstPts = performance.now() - t0 - (n * 1000) / AUDIO_RATE;
      const pts = firstPts + (samples * 1000) / AUDIO_RATE;
      samples += n;
      if (ws.readyState === WebSocket.OPEN) ws.send(packet(KIND_AUDIO, pts, ev.data));
      this.set({ audioSeconds: samples / AUDIO_RATE });
    };
  }

  private startVideo(stream: MediaStream, ws: WebSocket, t0: number, frameRate: number) {
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    void video.play();
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d')!;
    const period = 1000 / frameRate;
    let busy = false;

    const tick = () => {
      this.videoTimer = window.setTimeout(tick, period);
      if (busy || ws.readyState !== WebSocket.OPEN || !video.videoWidth) return;
      if (ws.bufferedAmount > MAX_BUFFERED) {
        this.set({ framesSkipped: this.state.framesSkipped + 1 });
        return;
      }
      const scale = Math.min(1, MAX_WIDTH / video.videoWidth, MAX_HEIGHT / video.videoHeight);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const pts = performance.now() - t0;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      busy = true;
      canvas.toBlob(
        async (blob) => {
          busy = false;
          if (!blob || ws.readyState !== WebSocket.OPEN) return;
          ws.send(packet(KIND_VIDEO, pts, await blob.arrayBuffer()));
          this.set({ framesSent: this.state.framesSent + 1 });
        },
        'image/jpeg',
        JPEG_QUALITY,
      );
    };
    tick();
  }

  private cleanup() {
    window.clearTimeout(this.videoTimer);
    this.videoTimer = undefined;
    this.state.stream?.getTracks().forEach((t) => t.stop());
    void this.audioCtx?.close();
    this.audioCtx = null;
    this.ws = null;
    this.set({ stream: null });
  }

  stop() {
    this.stopping = true;
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) {
      this.ws.close(1000, 'stopped');
    } else {
      this.cleanup();
      this.set({ status: 'idle' });
    }
  }
}

export const browserCapture = new BrowserCapture();
