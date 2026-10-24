import type { ReactNode } from 'react';

interface Props {
  variant: 'dark' | 'warm';
  children: ReactNode;
  compact?: boolean;
  className?: string;
}

/** Rounded bezel + notch on desktop; full-bleed on real phones. */
export function PhoneFrame({ variant, children, compact, className }: Props) {
  return (
    <div className={`phone phone--${variant} ${compact ? 'phone--compact' : ''} ${className ?? ''}`}>
      <div className="phone__notch" aria-hidden />
      <div className="phone__screen">{children}</div>
    </div>
  );
}

export function StatusBar({ right }: { right?: ReactNode }) {
  const now = new Date();
  const t = `${now.getHours()}:${String(now.getMinutes()).padStart(2, '0')}`;
  return (
    <div className="statusbar">
      <span>{t}</span>
      <span className="statusbar__right">{right}</span>
    </div>
  );
}
