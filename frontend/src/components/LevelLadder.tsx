import { LEVEL_NAMES, type Level } from '../lib/types';

/** The focal point: big level name + a thin 4-step meter. */
export function LevelLadder({ level, animKey }: { level: Level; animKey: number }) {
  return (
    <div className={`level level--${level}`}>
      <div key={animKey} className="level__now">
        <span className="level__num">{level}</span>
        <span className="level__name">{LEVEL_NAMES[level]}</span>
      </div>
      <div className="meter" role="img" aria-label={`Threat level ${level} of 3`}>
        {[0, 1, 2, 3].map((l) => (
          <div key={l} className={`meter__step meter__step--${l} ${l <= level ? 'is-on' : ''} ${l === level ? 'is-now' : ''}`}>
            <span className="meter__bar" />
            <span className="meter__label">{LEVEL_NAMES[l]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
