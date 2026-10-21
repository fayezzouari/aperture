import { useEffect, useRef } from "react";

/** How far the reader has scrolled through an element, written to its `--p`
 *  custom property as 0..1 so CSS can scrub animations with it.
 *
 *  - "enter": 0 when the element's top reaches `start` of the viewport height,
 *    1 when it reaches `end`. For effects that play as a block scrolls in.
 *  - "through": 0 when the element's top reaches the viewport top, 1 when its
 *    bottom does. For a tall wrapper around a sticky, pinned child.
 *
 *  Scrolling back runs the effect backwards. With reduced motion the property
 *  stays at 1, so every effect shows its final state. */
export function useScrollProgress<T extends HTMLElement>(mode: "enter" | "through" = "enter", start = 0.95, end = 0.35) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      el.style.setProperty("--p", "1");
      return;
    }
    let frame = 0;
    const update = () => {
      frame = 0;
      const rect = el.getBoundingClientRect();
      const vh = window.innerHeight;
      const raw =
        mode === "through"
          ? -rect.top / Math.max(1, rect.height - vh)
          : (vh * start - rect.top) / (vh * (start - end));
      el.style.setProperty("--p", Math.min(1, Math.max(0, raw)).toFixed(4));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      cancelAnimationFrame(frame);
    };
  }, [mode, start, end]);
  return ref;
}
