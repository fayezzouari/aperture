import { Mark } from "./Mark";

export function Nav() {
  return (
    <header className="nav">
      <a className="brand" href="#top" aria-label="Aperture">
        <Mark size={20} animate />
        Aperture
      </a>
      <nav className="nav-links" aria-label="Site">
        <a href="#ideas">How it works</a>
        <a href="#tour">Console</a>
        <a href="#workflows">Workflows</a>
        <a href="#operate">Operate</a>
        <a href="/docs/">Guide</a>
        <a className="btn btn-sm" href="#run">
          Run it locally
        </a>
      </nav>
    </header>
  );
}
