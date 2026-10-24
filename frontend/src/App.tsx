import { useEffect, useState } from 'react';
import Caller from './routes/Caller';
import Granny from './routes/Granny';
import Landing from './routes/Landing';
import Panel from './routes/Panel';
import Report from './routes/Report';
import Stage from './routes/Stage';

export default function App() {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const on = () => setPath(location.pathname);
    window.addEventListener('popstate', on);
    return () => window.removeEventListener('popstate', on);
  }, []);

  const p = path.replace(/\/+$/, '') || '/';
  if (p === '/caller') return <Caller />;
  if (p === '/granny') return <Granny />;
  if (p === '/panel') return <Panel />;
  if (p === '/stage') return <Stage />;
  const m = p.match(/^\/report\/(.+)$/);
  if (m) return <Report key={m[1]} id={decodeURIComponent(m[1])} />;
  return <Landing />;
}
