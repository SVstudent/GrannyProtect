export function PhoneIcon({ down }: { down?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="30" height="30" style={{ transform: down ? 'rotate(135deg)' : undefined }} aria-hidden>
      <path
        fill="currentColor"
        d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25c1.1.37 2.3.57 3.6.57a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1L6.6 10.8z"
      />
    </svg>
  );
}

export function MicIcon({ off }: { off?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden>
      <path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z" />
      {off && <path stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" d="M4 4l16 16" />}
    </svg>
  );
}

export function ScriptIcon() {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden>
      <path fill="currentColor" d="M5 4h14v2H5V4zm0 4h14v2H5V8zm0 4h10v2H5v-2zm0 4h7v2H5v-2z" />
    </svg>
  );
}
