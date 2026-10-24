/** The one product mark used on every page. */
export function Brand({ sub }: { sub?: string }) {
  return (
    <span className="brand">
      <svg className="brand__mark" viewBox="0 0 24 24" width="20" height="20" aria-hidden>
        <path fill="currentColor" d="M12 2 4 5v6c0 5 3.4 9.7 8 11 4.6-1.3 8-6 8-11V5l-8-3z" />
      </svg>
      <span className="brand__name">Granny-Protect 5000</span>
      {sub && <span className="brand__sub">{sub}</span>}
    </span>
  );
}
