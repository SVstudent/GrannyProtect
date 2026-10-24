// Shared phone-socket state for /caller and /granny.
import { useEffect, useState } from 'react';
import type { PhoneClientMsg, PhoneServerMsg, StateMsg } from './types';
import { useSocket } from './ws';

export function usePhone(role: 'caller' | 'granny', onMsg: (m: PhoneServerMsg) => void) {
  const { sock, connected, replaced } = useSocket(role);
  const [state, setState] = useState<StateMsg | null>(null);

  useEffect(() => {
    if (!sock) return;
    return sock.onJson((m: PhoneServerMsg) => {
      if (!m || typeof m !== 'object') return;
      if (m.type === 'state') setState(m);
      onMsg(m);
    });
    // onMsg is expected to be stable (useCallback / ref-based)
  }, [sock, onMsg]);

  const send = (m: PhoneClientMsg) => sock?.send(m) ?? false;
  return { sock, connected, replaced, state, send };
}
