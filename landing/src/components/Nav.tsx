export function Nav() {
  return (
    <header className="nav">
      <a className="wordmark" href="#top" aria-label="aperture, back to top">
        aperture
      </a>
      <nav className="nav-links" aria-label="Site">
        <a href="#ideas">Ideas</a>
        <a href="#validation">Validation</a>
        <a href="#workflows">Workflows</a>
        <a href="#operate">Operate</a>
        <a href="/docs/">Guide</a>
        <a className="nav-cta" href="#run">
          Run it locally →
        </a>
      </nav>
    </header>
  );
}
