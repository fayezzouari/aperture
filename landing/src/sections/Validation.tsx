import { SectionHead } from "../components/SectionHead";
import { useScrollProgress } from "../hooks/useScrollProgress";
import { DETECTIONS, LETTERBOX_SHIFT, PHOTO } from "../lib/detections";

/* Pinned while the reader scrolls through it. A version is `ready` once a
   runtime loads it, which says nothing about its config; the picture does.
   The masks start where a decoder that left the letterbox padding in puts
   them, LETTERBOX_SHIFT px low and dashed, and slide up as the page scrolls.
   When they reach the fruit they snap solid and the caption flips. */
export function Validation() {
  const ref = useScrollProgress<HTMLElement>("through");
  return (
    <section id="validation" className="validation" ref={ref}>
      <div className="pin">
        <SectionHead label="02 — validation" title={<>A status field says ready.<br />The picture disagrees.</>}>
          A version is ready once a runtime loads it. That proves the graph, not the config: leave the letterbox
          padding in and every outline drops {LETTERBOX_SHIFT} px. Run your own image through the playground and you see it.
        </SectionHead>
        <figure className="photo" style={{ ["--shift" as string]: `${LETTERBOX_SHIFT}px` }}>
          <img src={PHOTO.src} alt="A bowl of apples, oranges and a banana, with fruit-bowl-seg's 15 masks drawn over it." />
          <svg viewBox={`0 0 ${PHOTO.width} ${PHOTO.height}`} aria-hidden="true">
            <g className="masks wrong">
              {DETECTIONS.map((d) => (
                <polygon key={d.points} points={d.points} />
              ))}
            </g>
            <g className="masks right">
              {DETECTIONS.map((d) => (
                <polygon key={d.points} points={d.points} />
              ))}
            </g>
          </svg>
          <figcaption>
            <span className="caption wrong">padding not removed, every outline {LETTERBOX_SHIFT} px low</span>
            <span className="caption right">padding removed, {DETECTIONS.length} masks on their fruit</span>
            <span className="status">status: ready</span>
          </figcaption>
        </figure>
      </div>
    </section>
  );
}
