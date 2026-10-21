import { useScrollProgress } from "../hooks/useScrollProgress";

/** A region of a console screenshot, in the screenshot's own 2000×1250 space. */
export type Region = { x: number; y: number; w: number; h: number; label: string; right?: boolean };

const WIDTH = 2000;

/* A console screenshot read the way Aperture reads a photo: as it scrolls into
   view a scan line runs down it, and each region's box draws its edge the
   moment the line reaches it, label just after. Scrubbed by scroll, so
   scrolling back un-detects it. `crop` trims empty space off the bottom. */
export function Detected({ src, alt, crop, regions }: { src: string; alt: string; crop: number; regions: Region[] }) {
  const ref = useScrollProgress<HTMLDivElement>("enter", 0.9, 0.3);
  return (
    <div className="detected" ref={ref} style={{ aspectRatio: `${WIDTH} / ${crop}` }}>
      <img src={src} alt={alt} loading="lazy" />
      <svg viewBox={`0 0 ${WIDTH} ${crop}`} preserveAspectRatio="none" aria-hidden="true">
        {regions.map((r) => (
          <rect
            key={r.label}
            x={r.x}
            y={r.y}
            width={r.w}
            height={r.h}
            pathLength={1}
            style={{ ["--y" as string]: r.y / crop }}
          />
        ))}
      </svg>
      {regions.map((r) => (
        <span
          key={r.label}
          className={r.right ? "box-label right" : "box-label"}
          style={{
            ["--y" as string]: r.y / crop,
            top: `${(r.y / crop) * 100}%`,
            ...(r.right ? { right: `${((WIDTH - r.x - r.w) / WIDTH) * 100}%` } : { left: `${(r.x / WIDTH) * 100}%` }),
          }}
          aria-hidden="true"
        >
          {r.label}
        </span>
      ))}
      <i className="scan" aria-hidden="true" />
    </div>
  );
}
