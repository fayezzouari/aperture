import { Spiral } from "../components/Spiral";
import { useScrollProgress } from "../hooks/useScrollProgress";

/* Over the first 600 px of scroll the opening widens: rings scale out and
   twist, outer rings fade, the copy rises and fades. Browsers with scroll-driven
   animations run it off the main thread in CSS; the others follow `--p`. With
   reduced motion the hero stays at rest. */
export function Hero() {
  const ref = useScrollProgress<HTMLElement>("enter", 0, -0.66, 0);
  return (
    <section className="hero" ref={ref} aria-labelledby="hero-title">
      <Spiral className="opening" />
      <div className="hero-copy">
        <span className="label muted">a registry and gateway for vision models</span>
        <h1 id="hero-title">
          Many versions.
          <br />
          One opening.
        </h1>
        <p>Every model you ship stays in the registry. Apps only see the one tagged production, through a URL that never moves.</p>
        <div className="actions">
          <a className="btn-primary" href="#run">
            Run it locally
          </a>
          <a className="btn-text" href="#ideas">
            Read the three ideas →
          </a>
        </div>
      </div>
    </section>
  );
}
