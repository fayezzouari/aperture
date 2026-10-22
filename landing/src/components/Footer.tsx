import { WORDMARK } from "../lib/wordmark";

export function Footer() {
  return (
    <footer className="footer">
      <div className="footer-meta">
        <span>
          made by <a href="https://github.com/fayezzouari">fayez</a>
        </span>
      </div>
      <svg
        className="footer-word"
        viewBox={`0 0 ${WORDMARK.width} ${WORDMARK.height}`}
        style={{ ["--word-em" as string]: WORDMARK.width / WORDMARK.em }}
        role="img"
        aria-label="Aperture"
      >
        <defs>
          <linearGradient id="footer-word-fill" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2={WORDMARK.height}>
            <stop offset="0" stopColor="#ededed" />
            <stop offset="0.55" stopColor="#3a3a3e" />
            <stop offset="1" stopColor="#0e0e10" />
          </linearGradient>
        </defs>
        <path d={WORDMARK.d} fill="url(#footer-word-fill)" />
      </svg>
    </footer>
  );
}
