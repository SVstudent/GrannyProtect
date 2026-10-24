import { useEffect, useState } from 'react';

/** m:ss call timer that runs while `active`. */
export function useCallTimer(active: boolean) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    if (!active) return;
    setSecs(0);
    const t0 = Date.now();
    const id = window.setInterval(() => setSecs(Math.floor((Date.now() - t0) / 1000)), 500);
    return () => window.clearInterval(id);
  }, [active]);
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
}
