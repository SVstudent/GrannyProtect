import { PanelView } from '../components/PanelView';
import { usePanelFeed } from '../lib/panelState';

export default function Panel() {
  const { state, connected, clear } = usePanelFeed();
  return (
    <div className="page page--panel">
      <PanelView state={state} connected={connected} clear={clear} />
    </div>
  );
}
