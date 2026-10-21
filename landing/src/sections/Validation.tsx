import { SectionHead } from "../components/SectionHead";
import { useScrollProgress } from "../hooks/useScrollProgress";
import { DETECTIONS, LETTERBOX_SHIFT, PHOTO } from "../lib/detections";

/* Pinned while the reader scrolls through it. The masks start where a decoder
   that forgot the letterbox padding puts them, LETTERBOX_SHIFT px low and
   dashed, and slide up as the page scrolls. When they reach the fruit they
   snap solid and the caption flips: scrolling is the fix. */
export function Validation() {
  const ref = useScrollProgress<HTMLElement>("through");
  return (
    <section id="validation" className="validation" ref={ref}>
      <div className="pin">
        <SectionHead label="02 — validation" title={<>A status field says fine.<br />The picture disagrees.</>}>
          Same model, same photo. Forget to remove the letterbox padding and every outline drops {LETTERBOX_SHIFT} px. The
          status field can't see it. The stored overlay can.
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
            <span className="caption wrong">padding not removed · every outline {LETTERBOX_SHIFT} px low</span>
            <span className="caption right">stored reference overlay · {DETECTIONS.length} masks on their fruit</span>
            <span className="status">status: published</span>
          </figcaption>
        </figure>
      </div>
    </section>
  );
}
