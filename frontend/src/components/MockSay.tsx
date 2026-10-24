import { useState } from 'react';

interface Props {
  lines: string[];
  onSay: (text: string) => void;
  disabled?: boolean;
  variant: 'dark' | 'warm';
}

/** Mock-mode typed lines + quick-line buttons. */
export function MockSay({ lines, onSay, disabled, variant }: Props) {
  const [text, setText] = useState('');
  const submit = () => {
    const t = text.trim();
    if (!t) return;
    onSay(t);
    setText('');
  };
  return (
    <div className={`mocksay mocksay--${variant}`}>
      <div className="mocksay__lines">
        {lines.map((l) => (
          <button key={l} className="mocksay__chip" disabled={disabled} onClick={() => onSay(l)} title={l}>
            {l}
          </button>
        ))}
      </div>
      <form
        className="mocksay__row"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a line to say…" disabled={disabled} />
        <button type="submit" disabled={disabled || !text.trim()}>
          Say
        </button>
      </form>
    </div>
  );
}
