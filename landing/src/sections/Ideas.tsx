import { Detected, type Region } from "../components/Detected";
import { SectionHead } from "../components/SectionHead";

type Idea = { n: string; title: string; body: string; shot: { src: string; alt: string; crop: number; regions: Region[] } };

/* Each idea is shown on the console screen that proves it: fruit-bowl-seg v1
   in the demo workspace, captured by scripts/landing_screenshots.py. Regions
   are measured in the screenshots' 2000×1250 space. */
const ideas: Idea[] = [
  {
    n: "i.",
    title: "Config travels with the version",
    body: "Preprocessing, decoder, thresholds and the class list live on the version. v1 is published, so its config is frozen: a retrain that needs a different resize is a new version.",
    shot: {
      src: "/screenshots/landing/config.webp",
      alt: "The Config tab of fruit-bowl-seg: v1 is ready and its letterbox resize and yolo_seg decoder are read-only.",
      crop: 1060,
      regions: [
        { x: 1231, y: 247, w: 134, h: 43, label: "v1 frozen", right: true },
        { x: 379, y: 484, w: 986, h: 172, label: "letterbox resize" },
        { x: 379, y: 886, w: 986, h: 164, label: "yolo_seg decoder" },
      ],
    },
  },
  {
    n: "ii.",
    title: "Tags resolve per request",
    body: "Products call production, which points at v1. Ship v2 and move the tag: the next request follows, nothing redeploys, and v1 stays one click away.",
    shot: {
      src: "/screenshots/landing/versions.webp",
      alt: "The Versions tab of fruit-bowl-seg: v1 carries the production tag, with a Tag button to move it.",
      crop: 470,
      regions: [
        { x: 356, y: 237, w: 738, h: 26, label: "tags move, files never do" },
        { x: 542, y: 312, w: 115, h: 28, label: "production → v1" },
        { x: 1829, y: 313, w: 56, h: 35, label: "move tag", right: true },
      ],
    },
  },
  {
    n: "iii.",
    title: "Validation is an overlay, not a checkmark",
    body: "The playground runs the exact serving path, through the production tag, and draws what came back: 18 masks on the fruit-bowl photo, with per-stage timings. A wrong letterbox is obvious here and silent in a status field.",
    shot: {
      src: "/screenshots/landing/playground.webp",
      alt: "The Playground of fruit-bowl-seg: the fruit-bowl photo with 18 segmentation masks and a latency breakdown per stage.",
      crop: 1250,
      regions: [
        { x: 379, y: 403, w: 425, h: 44, label: "production → v1" },
        { x: 874, y: 345, w: 1068, h: 140, label: "stage timings" },
        { x: 926, y: 618, w: 964, h: 632, label: "18 masks, stored overlay" },
      ],
    },
  },
];

export function Ideas() {
  return (
    <section id="ideas" className="ideas">
      <SectionHead label="01 — three ideas" title={<>Three ideas.<br />Everything else follows.</>}>
        The rest of aperture is consequence. Learn these three and the console explains itself.
      </SectionHead>
      <div className="idea-list">
        {ideas.map((idea) => (
          <article className="idea" key={idea.n}>
            <header>
              <h3>
                <span className="label">{idea.n}</span>
                {idea.title}
              </h3>
              <p>{idea.body}</p>
            </header>
            <Detected {...idea.shot} />
          </article>
        ))}
      </div>
    </section>
  );
}
