import { Mark } from "./Mark";

export function Nav() {
  return (
    <header className="nav">
      <a className="wordmark" href="#top" aria-label="Aperture, back to top">
        <Mark size={30} />
        Aperture
      </a>
      <nav className="nav-links" aria-label="Site">
        <a href="#ideas">Ideas</a>
        <a href="#overrides">Overrides</a>
        <a href="#workflows">Workflows</a>
        <a href="#operate">Operate</a>
        <a href="#run">Run it locally</a>
        <a className="nav-cta" href="/docs/">
          Guide →
        </a>
      </nav>
    </header>
  );
}
