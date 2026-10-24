// Reconnecting WebSocket wrapper. Consumers subscribe to the wrapper, never the raw socket,
// so handlers survive reconnects.
import { useEffect, useRef, useState } from 'react';

export function wsUrl(role: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}/ws/${role}`;
}

type JsonHandler = (msg: any) => void;
type BinHandler = (buf: ArrayBuffer) => void;
type StatusHandler = (open: boolean) => void;

export class ReconnectingWS {
  private ws: WebSocket | null = null;
  private jsonHandlers = new Set<JsonHandler>();
  private binHandlers = new Set<BinHandler>();
  private statusHandlers = new Set<StatusHandler>();
  private closed = false;
  /** True when another tab or device took over this phone role. */
  replaced = false;
  private retry = 0;
  private timer: number | undefined;
  open = false;

  constructor(private url: string) {
    this.connect();
  }

  private connect() {
    if (this.closed) return;
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch {
      this.schedule();
      return;
    }
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.setOpen(true);
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        let msg: unknown;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        this.jsonHandlers.forEach((h) => h(msg));
      } else if (ev.data instanceof ArrayBuffer) {
        const buf = ev.data;
        this.binHandlers.forEach((h) => h(buf));
      }
    };
    ws.onclose = (ev) => {
      if (this.ws === ws) {
        this.ws = null;
        this.setOpen(false);
        // 4000 = this phone was opened in another tab/device. Don't reconnect, or the two tabs
        // would keep kicking each other off the line.
        if (ev.code === 4000) {
          this.closed = true;
          this.replaced = true;
          this.statusHandlers.forEach((h) => h(false));
          return;
        }
        this.schedule();
      }
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    };
  }

  private schedule() {
    if (this.closed) return;
    const delay = Math.min(5000, 400 * 2 ** this.retry++);
    window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => this.connect(), delay);
  }

  private setOpen(v: boolean) {
    this.open = v;
    this.statusHandlers.forEach((h) => h(v));
  }

  send(data: object | ArrayBuffer) {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(data instanceof ArrayBuffer ? data : JSON.stringify(data));
    return true;
  }

  onJson(h: JsonHandler) {
    this.jsonHandlers.add(h);
    return () => void this.jsonHandlers.delete(h);
  }
  onBinary(h: BinHandler) {
    this.binHandlers.add(h);
    return () => void this.binHandlers.delete(h);
  }
  onStatus(h: StatusHandler) {
    this.statusHandlers.add(h);
    return () => void this.statusHandlers.delete(h);
  }

  close() {
    this.closed = true;
    window.clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
  }
}

/** Owns one ReconnectingWS for the component lifetime. */
export function useSocket(role: string) {
  const [sock, setSock] = useState<ReconnectingWS | null>(null);
  const [connected, setConnected] = useState(false);
  const ref = useRef<ReconnectingWS | null>(null);
  useEffect(() => {
    const s = new ReconnectingWS(wsUrl(role));
    ref.current = s;
    setSock(s);
    const off = s.onStatus(setConnected);
    return () => {
      off();
      s.close();
    };
  }, [role]);
  // When another tab/device takes this phone role, this one stops for good (see onclose code 4000).
  const replaced = !connected && !!sock?.replaced;
  return { sock, connected, replaced };
}

/** Tiny fetch helpers that never throw. */
export async function getJson<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(path);
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export async function postJson(path: string, body?: unknown): Promise<boolean> {
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return r.ok;
  } catch {
    return false;
  }
}

export function navigate(path: string) {
  history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
