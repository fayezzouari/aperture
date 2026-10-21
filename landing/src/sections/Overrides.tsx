import { useState } from "react";
import { SectionHead } from "../components/SectionHead";
import { useScrollProgress } from "../hooks/useScrollProgress";
import { DETECTIONS, PHOTO } from "../lib/detections";

/* Per-request overrides: any call can set its own score threshold, and the
   deployed config stays as it is. Pinned while the reader scrolls through it,
   the threshold falls from HIGH to LOW and fruit-bowl-seg's real masks appear
   in score order, while the request beside the photo prints the value it
   would send. */
const HIGH = 0.85;
const LOW = 0.25;

export function Overrides() {
  const [threshold, setThreshold] = useState(HIGH);
  const ref = useScrollProgress<HTMLElement>("through", 0.95, 0.35, 1, (p) =>
    setThreshold(Math.round((HIGH - p * (HIGH - LOW)) * 100) / 100),
  );
  const shown = DETECTIONS.filter((d) => d.score >= threshold).length;

  return (
    <section id="overrides" className="overrides" ref={ref} style={{ ["--high" as string]: HIGH, ["--low" as string]: LOW }}>
      <div className="pin">
        <SectionHead label="02 — overrides" title={<>One deploy.<br />Every threshold.</>}>
          Each request can set its own score threshold, IoU and top-k. The deployed config stays as it is, so one product keeps
          only the sure detections while another takes everything. Scroll to lower the threshold on fruit-bowl-seg's real
          output.
        </SectionHead>
        <div className="override-stage">
          <figure className="photo">
            <img src={PHOTO.src} alt="A bowl of apples, oranges and a banana, with fruit-bowl-seg's masks drawn over the fruit." />
            <svg viewBox={`0 0 ${PHOTO.width} ${PHOTO.height}`} aria-hidden="true">
              {DETECTIONS.map((d) => (
                <polygon key={d.points} points={d.points} style={{ ["--s" as string]: d.score }} />
              ))}
            </svg>
          </figure>
          <aside className="request" aria-live="polite">
            <span className="label muted">the request</span>
            <pre>
              {`curl -X POST \\\n  "$AP/v1/models/fruit-bowl-seg/production/predict" \\\n  -F "images=@fruit-bowl.jpg" \\\n  -F 'overrides={"score_threshold":${threshold.toFixed(2)}}'`}
            </pre>
            <div className="readout">
              <b>
                {shown} of {DETECTIONS.length}
              </b>
              <span>masks above {threshold.toFixed(2)}</span>
            </div>
            <span className="untouched">deployed config untouched</span>
          </aside>
        </div>
      </div>
    </section>
  );
}
