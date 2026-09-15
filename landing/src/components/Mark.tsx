/** The console's logo mark. `animate` swings the blades open once on load. */
export function Mark({ size = 22, animate = false }: { size?: number; animate?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <circle cx="16" cy="16" r="10" fill="none" stroke="#ededed" strokeWidth="2" />
      <g className={animate ? "blades" : undefined}>
        <path d="M16 6v20M7.1 11l17.8 10M7.1 21l17.8-10" stroke="#ededed" strokeWidth="1.1" opacity="0.5" fill="none" />
      </g>
    </svg>
  );
}
