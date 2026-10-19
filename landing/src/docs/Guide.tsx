import { useEffect, useState } from "react";
import { Mark } from "../components/Mark";
import { chapters } from "./chapters";

/** The guide: the console's sidebar on the left, one long page of chapters on the right. */
export function Guide() {
  const active = useActiveSection();
  const [menuOpen, setMenuOpen] = useState(false);
  const groups = [...new Set(chapters.map((chapter) => chapter.group))];

  const toc = (
    <nav className="toc" aria-label="Guide">
      {groups.map((group) => (
        <div key={group} className="toc-group">
          <p className="toc-label">{group}</p>
          <ul>
            {chapters
              .filter((chapter) => chapter.group === group)
              .map((chapter) => {
                const inChapter = active === chapter.id || chapter.sections.some((section) => section.id === active);
                return (
                  <li key={chapter.id}>
                    <a href={`#${chapter.id}`} aria-current={inChapter ? "true" : undefined} onClick={() => setMenuOpen(false)}>
                      {chapter.title}
                    </a>
                    {inChapter ? (
                      <ul className="toc-sections">
                        {chapter.sections.map((section) => (
                          <li key={section.id}>
                            <a href={`#${section.id}`} aria-current={active === section.id ? "true" : undefined} onClick={() => setMenuOpen(false)}>
                              {section.title}
                            </a>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                );
              })}
          </ul>
        </div>
      ))}
    </nav>
  );

  return (
    <div className="wrap docs-wrap">
      <header className="nav">
        <a className="brand" href="/" aria-label="Aperture home">
          <Mark size={20} />
          Aperture
          <span className="badge">Guide</span>
        </a>
        <nav className="nav-links" aria-label="Site">
          <a href="/">Overview</a>
          <a href="/#run">Run it locally</a>
          <button type="button" className="btn btn-sm docs-menu" onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen}>
            Contents
          </button>
        </nav>
      </header>

      <div className="docs">
        <aside className={`docs-side ${menuOpen ? "open" : ""}`}>{toc}</aside>
        <main className="docs-main" id="top">
          <div className="docs-hero">
            <p className="eyebrow">User guide</p>
            <h1>Everything the console does, one step at a time.</h1>
            <p className="lede">
              From signing in to rolling back a release. Every screenshot was taken in the <code>e2e</code> workspace that the end-to-end
              check builds, so what you see is what a fresh install looks like after one real run.
            </p>
          </div>
          {chapters.map((chapter) => (
            <section key={chapter.id} id={chapter.id} className="chapter">
              <p className="eyebrow">{chapter.group}</p>
              <h2>{chapter.title}</h2>
              <div className="chapter-lede">{chapter.lede}</div>
              {chapter.sections.map((section) => (
                <div key={section.id} id={section.id} className="docs-section">
                  <h3>
                    <a href={`#${section.id}`}>{section.title}</a>
                  </h3>
                  {section.body}
                </div>
              ))}
            </section>
          ))}
        </main>
      </div>

      <footer>
        <span>Aperture v0.1.0 · self-hosted · no outbound calls at runtime</span>
        <a href="#top">Back to top</a>
      </footer>
    </div>
  );
}

/** The id of the chapter or section nearest the top of the viewport, for the sidebar highlight. */
function useActiveSection(): string | null {
  const [active, setActive] = useState<string | null>(null);
  useEffect(() => {
    const targets = [...document.querySelectorAll<HTMLElement>(".chapter, .docs-section")];
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-64px 0px -70% 0px" },
    );
    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, []);
  return active;
}
