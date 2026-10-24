import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

// No <StrictMode>: its dev double-mount would open two phone sockets.
createRoot(document.getElementById('root')!).render(<App />);
