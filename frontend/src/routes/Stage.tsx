import { useEffect, useState } from 'react';
import { PanelView } from '../components/PanelView';
import { PhoneFrame, StatusBar } from '../components/PhoneFrame';
import { usePanelFeed, type PanelState } from '../lib/panelState';
import { LEVEL_NAMES } from '../lib/types';

/** Mini granny phone reconstructed from panel events. */
function GrannyMirror({ state }: { state: PanelState }) {
  const [sub, setSub] = useState<string | null>(null);
  useEffect(() => {
    if (!state.lastWhisper) return setSub(null);
    setSub(state.lastWhisper.text);
    const id = window.setTimeout(() => setSub(null), 9000);
    return () => window.clearTimeout(id);
  }, [state.lastWhisper]);

  const cs = state.callState === 'unknown' ? 'idle' : state.callState;
  const lvl = state.level;
  return (
    <div className="mirror">
      <div className="mirror__caption">What granny hears</div>
      <PhoneFrame variant="warm" compact>
        <StatusBar />
        <div className={`mirror__screen mirror__screen--l${lvl}`}>
          <div className="mirror__state">{cs === 'active' ? 'On a call' : cs === 'ringing' ? 'Incoming call…' : cs === 'ended' ? 'Call ended' : 'Idle'}</div>
          <div className={`mirror__orb ${cs === 'active' ? 'is-live' : ''} ${sub ? 'is-whisper' : ''}`} data-level={lvl} />
          <div className={`mirror__sub ${sub ? 'is-on' : ''}`}>{sub ? `“${sub}”` : ''}</div>
        </div>
      </PhoneFrame>
      <div className={`mirror__level lvl lvl--${lvl}`}>
        Level {lvl} · {LEVEL_NAMES[lvl]}
      </div>
    </div>
  );
}

export default function Stage() {
  const { state, connected, clear } = usePanelFeed();
  return (
    <div className="page page--stage">
      <GrannyMirror state={state} />
      <div className="stage__panel">
        <PanelView state={state} connected={connected} clear={clear} embedded />
      </div>
    </div>
  );
}
