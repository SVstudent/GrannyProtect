import { useEffect, useRef } from 'react';

interface Props {
  analysers: (AnalyserNode | null)[];
  glow?: 'none' | 'caution' | 'alert';
  size?: number;
  color?: string;
  label?: string;
}

const GLOW: Record<string, string> = {
  none: 'rgba(255, 196, 140, 0)',
  caution: 'rgba(255, 179, 0, 0.75)',
  alert: 'rgba(239, 68, 68, 0.8)',
};

/** Pulsing circular waveform driven by one or more AnalyserNodes. */
export function CallBubble({ analysers, glow = 'none', size = 280, color = '#f59e5b', label }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const ansRef = useRef(analysers);
  ansRef.current = analysers;

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    c.width = size * dpr;
    c.height = size * dpr;
    const g = c.getContext('2d')!;
    let raf = 0;
    let level = 0;
    const bufs = new Map<AnalyserNode, Uint8Array>();
    const N = 96;
    const smooth = new Float32Array(N);
    const t0 = performance.now();

    const draw = () => {
      raf = requestAnimationFrame(draw);
      const bins = new Float32Array(N);
      let rms = 0;
      let count = 0;
      for (const an of ansRef.current) {
        if (!an) continue;
        let b = bufs.get(an);
        if (!b) {
          b = new Uint8Array(an.fftSize);
          bufs.set(an, b);
        }
        an.getByteTimeDomainData(b);
        let sum = 0;
        for (let i = 0; i < b.length; i++) {
          const v = (b[i] - 128) / 128;
          sum += v * v;
        }
        rms = Math.max(rms, Math.sqrt(sum / b.length));
        const step = Math.floor(b.length / N);
        for (let i = 0; i < N; i++) bins[i] = Math.max(bins[i], Math.abs((b[i * step] - 128) / 128));
        count++;
      }
      level += (Math.min(1, rms * 4) - level) * 0.18;
      const t = (performance.now() - t0) / 1000;
      const W = c.width;
      const cx = W / 2;
      const base = W * 0.27;
      g.clearRect(0, 0, W, W);

      // breathing halo
      const breathe = 0.5 + 0.5 * Math.sin(t * 1.6);
      const halo = base * (1.25 + level * 0.5 + breathe * 0.05);
      const grad = g.createRadialGradient(cx, cx, base * 0.6, cx, cx, halo * 1.3);
      grad.addColorStop(0, hexA(color, 0.35));
      grad.addColorStop(1, hexA(color, 0));
      g.fillStyle = grad;
      g.beginPath();
      g.arc(cx, cx, halo * 1.3, 0, Math.PI * 2);
      g.fill();

      // waveform ring
      g.beginPath();
      for (let i = 0; i <= N; i++) {
        const k = i % N;
        smooth[k] += ((count ? bins[k] : 0) - smooth[k]) * 0.3;
        const idle = 0.015 * Math.sin(t * 2 + k * 0.4);
        const r = base * (1.02 + level * 0.18 + smooth[k] * 0.35 + idle);
        const a = (k / N) * Math.PI * 2 - Math.PI / 2;
        const x = cx + Math.cos(a) * r;
        const y = cx + Math.sin(a) * r;
        if (i === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
      g.closePath();
      g.fillStyle = hexA(color, 0.22);
      g.fill();
      g.lineWidth = 3 * dpr;
      g.strokeStyle = hexA(color, 0.85);
      g.stroke();

      // core
      const core = base * (0.86 + level * 0.12);
      const cg = g.createRadialGradient(cx - core * 0.3, cx - core * 0.3, core * 0.1, cx, cx, core);
      cg.addColorStop(0, '#fff7ee');
      cg.addColorStop(1, color);
      g.fillStyle = cg;
      g.beginPath();
      g.arc(cx, cx, core, 0, Math.PI * 2);
      g.fill();
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [size, color]);

  return (
    <div className="bubble" style={{ width: size, height: size, ['--glow' as string]: GLOW[glow] }} data-glow={glow}>
      <canvas ref={canvas} style={{ width: size, height: size }} />
      {label && <div className="bubble__label">{label}</div>}
    </div>
  );
}

function hexA(hex: string, a: number) {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((x) => x + x).join('') : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}
