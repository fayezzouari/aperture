import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { fmt } from "../lib/format";
import { canvasStep, classColor, classColorCss, fillPolygons, paintRle, reuseMaskLayer,
         type MaskLayer, type MaskPayload } from "../lib/masks";
import { Button } from "./ui";

/**
 * The result viewer: what the model returned, drawn over the image it saw.
 *
 * A box is the least a prediction has to say. When a model returns masks,
 * keypoints or a class distribution, showing only boxes throws away the part
 * that is hard to produce — so this draws whatever came back, and lets you
 * decide what to look at: masks on their own, boxes on their own, one instance
 * isolated, one class at a time, or the score floor moved.
 */

export type Keypoint = { name?: string | null; x: number; y: number; score: number };

export type Instance = {
  box: number[];
  score: number;
  class_id: number;
  class_name: string;
  mask?: MaskPayload | null;
  keypoints?: Keypoint[] | null;
};

export type ResultPayload = {
  index?: number;
  width?: number;
  height?: number;
  detections?: Instance[];
  segments?: Instance[];
  classifications?: { class_id: number; class_name: string; score: number }[];
  texts?: { text: string; score: number; box?: number[] | null }[];
  embedding?: number[];
  embedding_dim?: number;
};

const SKELETON: [number, number][] = [
  [0, 1], [0, 2], [1, 3], [2, 4], [5, 6], [5, 7], [7, 9], [6, 8], [8, 10],
  [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
];

/** A capture that may no longer exist, shown without a broken-image icon. */
export function CaptureImage({ url, alt }: { url: string; alt: string }) {
  const [missing, setMissing] = useState(false);
  useEffect(() => setMissing(false), [url]);
  if (missing)
    return (
      <div className="flex aspect-[4/3] w-full items-center justify-center rounded-lg border border-line bg-[#101014] text-[11.5px] text-ink-3">
        image no longer stored
      </div>
    );
  return (
    <img
      src={url}
      alt={alt}
      className="w-full rounded-lg border border-line object-contain"
      onError={() => setMissing(true)}
    />
  );
}

export function ResultViewer({
  imageUrl,
  result,
  maxHeight = 520,
}: {
  imageUrl: string;
  result?: ResultPayload | null;
  maxHeight?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [showMasks, setShowMasks] = useState(true);
  const [showBoxes, setShowBoxes] = useState(true);
  const [showLabels, setShowLabels] = useState(true);
  const [tuned, setTuned] = useState<string | null>(null);
  const [opacity, setOpacity] = useState(0.45);
  const [minScore, setMinScore] = useState(0);
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [hovered, setHovered] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
  // what the mask layer painted where, so the pointer can be tested against the
  // shape instead of the box it happens to sit in
  const hitRef = useRef<{ layer: MaskLayer; owners: Map<number, number> } | null>(null);
  const poolRef = useRef<MaskLayer | null>(null);
  const active = pinned ?? hovered;
  const [missing, setMissing] = useState(false);

  // a capture is stored under the model slug, so a log can outlive its image
  // (the model was deleted, or retention swept the object). The predictions are
  // still worth looking at, so draw them over a placeholder instead of leaving
  // the browser's broken-image icon.
  useEffect(() => setMissing(false), [imageUrl]);

  const width = result?.width ?? 0;
  const height = result?.height ?? 0;

  // segments carry the masks; detections carry the boxes. For a segmentation
  // model they are the same list, so prefer the richer one and never draw twice.
  const instances: Instance[] = useMemo(() => {
    const segments = result?.segments ?? [];
    const detections = result?.detections ?? [];
    return segments.length ? segments : detections;
  }, [result]);

  // an inference log stores masks elided (format and area, no pixels), so having
  // a mask object is not the same as having something to draw
  const drawable = (mask?: MaskPayload | null) =>
    Boolean(mask && (mask.counts || mask.polygons?.length));
  const hasMasks = instances.some((item) => drawable(item.mask));

  /**
   * Sensible defaults for *this* result, not for results in general.
   *
   * A model that returned 27 instances is unreadable with every box and label
   * drawn at once — the annotations cover the thing you are trying to look at.
   * So: masks carry the shape when there are masks, boxes step aside, and labels
   * only appear when there are few enough to read. Hovering always names one.
   */
  const signature = `${instances.length}:${hasMasks}`;
  useEffect(() => {
    if (tuned === signature) return;
    setShowMasks(hasMasks);
    setShowBoxes(!hasMasks || instances.length <= 8);
    setShowLabels(instances.length <= 8);
    setTuned(signature);
  }, [signature, tuned, hasMasks, instances.length]);

  const classes = useMemo(() => {
    const counts = new Map<number, { name: string; count: number }>();
    for (const item of instances) {
      const entry = counts.get(item.class_id);
      if (entry) entry.count += 1;
      else counts.set(item.class_id, { name: item.class_name, count: 1 });
    }
    return [...counts.entries()]
      .map(([classId, value]) => ({ classId, ...value }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }, [instances]);

  const visible = useMemo(
    () =>
      instances
        .map((item, index) => ({ item, index }))
        .filter(({ item }) => item.score >= minScore && !hidden.has(item.class_id)),
    [instances, minScore, hidden]
  );

  // masks go on a canvas: decoding RLE to a path would be lossy, and painting
  // only the foreground runs keeps a full-resolution instance mask cheap
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !width || !height) return;
    const step = canvasStep(width, height);
    const bufferWidth = Math.max(1, Math.ceil(width / step));
    const bufferHeight = Math.max(1, Math.ceil(height / step));
    canvas.width = bufferWidth;
    canvas.height = bufferHeight;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, bufferWidth, bufferHeight);
    if (!showMasks || !hasMasks) return;

    const layer = reuseMaskLayer(poolRef.current, bufferWidth, bufferHeight);
    poolRef.current = layer;
    const owners = new Map<number, number>();
    let painted = false;
    for (const { item, index } of visible) {
      if (!drawable(item.mask)) continue;
      const dimmed = active !== null && active !== index;
      const alpha = dimmed ? opacity * 0.25 : opacity;
      if (item.mask!.format === "polygon" && item.mask!.polygons?.length) {
        fillPolygons(context, item.mask!, classColorCss(item.class_id, alpha), step,
                     classColorCss(item.class_id, Math.min(1, alpha * 1.9 + 0.25)));
      } else if (item.mask!.counts) {
        paintRle(layer, item.mask!, classColor(item.class_id), alpha, step);
        owners.set(layer.stamp, index);  // the stamp paintRle just used
        painted = true;
      }
    }
    hitRef.current = painted ? { layer, owners } : null;
    if (painted) {
      // composite the decoded masks under whatever the polygon path already drew
      const scratch = document.createElement("canvas");
      scratch.width = bufferWidth;
      scratch.height = bufferHeight;
      const image = context.createImageData(bufferWidth, bufferHeight);
      image.data.set(layer.pixels);
      scratch.getContext("2d")?.putImageData(image, 0, 0);
      context.drawImage(scratch, 0, 0);
    }
  }, [visible, showMasks, opacity, active, width, height, hasMasks]);

  /**
   * Which instance is under the pointer.
   *
   * Testing the pointer against a box selects whatever rectangle contains it,
   * and boxes overlap heavily on a photo of one object - so pointing at a door
   * would select the wing whose box happens to cover it. The mask layer already
   * recorded which instance painted each pixel, so the shape can answer instead:
   * point at a shape, get that shape, and the answer matches what is drawn on
   * top at that pixel. Results with no mask to test fall back to their boxes.
   */
  const maskHover = showMasks && hasMasks;

  const instanceAt = (event: MouseEvent<HTMLDivElement>): number | null => {
    const canvas = canvasRef.current;
    const store = hitRef.current;
    if (!canvas || !store) return null;
    const box = canvas.getBoundingClientRect();
    if (!box.width || !box.height) return null;
    const x = Math.floor(((event.clientX - box.left) / box.width) * canvas.width);
    const y = Math.floor(((event.clientY - box.top) / box.height) * canvas.height);
    if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) return null;
    const stamp = store.layer.owner[y * canvas.width + x];
    return stamp ? store.owners.get(stamp) ?? null : null;
  };

  const strokeWidth = Math.max(1.5, width / 420);
  const labelHeight = height / 34;

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-[#08080a]">
      {instances.length ? (
        <div className="toolbar">
          <div className="segmented" role="group" aria-label="Overlay layers">
            {hasMasks ? (
              <button type="button" aria-pressed={showMasks} onClick={() => setShowMasks((v) => !v)}>
                Masks
              </button>
            ) : null}
            <button type="button" aria-pressed={showBoxes} onClick={() => setShowBoxes((v) => !v)}>
              Boxes
            </button>
            <button type="button" aria-pressed={showLabels} onClick={() => setShowLabels((v) => !v)}>
              Labels
            </button>
          </div>

          {hasMasks && showMasks ? (
            <label className="flex items-center gap-2 text-[11.5px] text-ink-3">
              Opacity
              <input
                type="range"
                min={10}
                max={90}
                value={Math.round(opacity * 100)}
                onChange={(event) => setOpacity(Number(event.target.value) / 100)}
                className="w-[76px]"
                aria-label="Mask opacity"
              />
            </label>
          ) : null}

          <label className="flex items-center gap-2 text-[11.5px] text-ink-3">
            Score ≥ {minScore.toFixed(2)}
            <input
              type="range"
              min={0}
              max={99}
              value={Math.round(minScore * 100)}
              onChange={(event) => setMinScore(Number(event.target.value) / 100)}
              className="w-[86px]"
              aria-label="Minimum score"
            />
          </label>

          <span className="ml-auto text-[11.5px] text-ink-3">
            showing {visible.length} of {instances.length}
            {active !== null ? " · 1 isolated" : ""}
          </span>
          {active !== null ? (
            <Button size="sm" variant="ghost"
                    onClick={() => { setPinned(null); setHovered(null); }}>
              Clear
            </Button>
          ) : null}
        </div>
      ) : null}

      {/* the overlays are positioned against the image, not the panel: the image is
          centred and narrower than the panel, so anchoring to the panel stretches
          every mask sideways and lands it off the object */}
      <div className="flex items-center justify-center" style={{ maxHeight }}>
        <div
          className="relative inline-block leading-none"
          style={{ maxHeight, cursor: maskHover ? "pointer" : undefined }}
          onMouseMove={maskHover ? (event) => setHovered(instanceAt(event)) : undefined}
          onMouseLeave={maskHover ? () => setHovered(null) : undefined}
          onClick={maskHover ? (event) => {
            const index = instanceAt(event);
            setPinned((current) => (index !== null && current === index ? null : index));
          } : undefined}
        >
        {missing ? (
          <div
            className="flex w-full items-center justify-center bg-[#101014] text-[11.5px] text-ink-3"
            style={{ maxHeight, aspectRatio: width && height ? `${width} / ${height}` : "4 / 3" }}
          >
            image no longer stored — predictions only
          </div>
        ) : (
          <img
            src={imageUrl}
            alt="Prediction input"
            className="block max-h-full w-auto max-w-full object-contain"
            style={{ maxHeight }}
            onError={() => setMissing(true)}
          />
        )}
        {width && height ? (
          <>
            <canvas
              ref={canvasRef}
              className="pointer-events-none absolute inset-0 h-full w-full"
            />
            <svg
              viewBox={`0 0 ${width} ${height}`}
              className="absolute inset-0 h-full w-full"
              preserveAspectRatio="xMidYMid meet"
              style={{ pointerEvents: maskHover ? "none" : undefined }}
            >
              {visible.map(({ item, index }) => {
                const dimmed = active !== null && active !== index;
                const color = classColorCss(item.class_id);
                const [x1, y1, x2, y2] = item.box;
                const label = `${item.class_name} ${item.score.toFixed(2)}`;
                return (
                  <g
                    key={index}
                    opacity={dimmed ? 0.25 : 1}
                    onMouseEnter={() => setHovered(index)}
                    onMouseLeave={() => setHovered(null)}
                    onClick={() => setPinned(pinned === index ? null : index)}
                    style={{ cursor: "pointer" }}
                  >
                    <rect
                      x={x1}
                      y={y1}
                      width={Math.max(1, x2 - x1)}
                      height={Math.max(1, y2 - y1)}
                      fill="transparent"
                      stroke={showBoxes || active === index ? color : "transparent"}
                      strokeWidth={strokeWidth}
                    />
                    {showLabels || active === index ? (
                      <>
                        <rect
                          x={x1}
                          y={Math.max(0, y1 - labelHeight)}
                          width={(label.length + 1) * (width / 78)}
                          height={labelHeight}
                          fill={color}
                        />
                        <text
                          x={x1 + width / 300}
                          y={Math.max(0, y1 - labelHeight) + labelHeight * 0.74}
                          fontSize={width / 56}
                          fill="#0a0a0a"
                          className="mono"
                        >
                          {label}
                        </text>
                      </>
                    ) : null}
                    {item.keypoints?.length ? (
                      <>
                        {SKELETON.map(([a, b]) =>
                          item.keypoints![a]?.score > 0.3 && item.keypoints![b]?.score > 0.3 ? (
                            <line
                              key={`${a}-${b}`}
                              x1={item.keypoints![a].x}
                              y1={item.keypoints![a].y}
                              x2={item.keypoints![b].x}
                              y2={item.keypoints![b].y}
                              stroke={color}
                              strokeWidth={strokeWidth * 0.8}
                            />
                          ) : null
                        )}
                        {item.keypoints.map((point, k) =>
                          point.score > 0.3 ? (
                            <circle
                              key={k}
                              cx={point.x}
                              cy={point.y}
                              r={strokeWidth * 1.6}
                              fill="#fff"
                              stroke={color}
                              strokeWidth={strokeWidth * 0.6}
                            />
                          ) : null
                        )}
                      </>
                    ) : null}
                  </g>
                );
              })}
            </svg>
          </>
        ) : null}
        </div>
      </div>

      {classes.length > 1 ? (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-line px-3 py-2">
          {classes.map(({ classId, name, count }) => {
            const off = hidden.has(classId);
            return (
              <button
                key={classId}
                onClick={() =>
                  setHidden((current) => {
                    const next = new Set(current);
                    if (next.has(classId)) next.delete(classId);
                    else next.add(classId);
                    return next;
                  })
                }
                className={`badge ${off ? "opacity-40" : ""}`}
                title={off ? "Show this class" : "Hide this class"}
              >
                <span
                  className="inline-block h-[8px] w-[8px] rounded-[2px]"
                  style={{ background: classColorCss(classId) }}
                  aria-hidden
                />
                {name}
                <span className="num text-ink-3">{count}</span>
              </button>
            );
          })}
          {hidden.size ? (
            <Button size="sm" variant="ghost" onClick={() => setHidden(new Set())}>
              Show all
            </Button>
          ) : null}
        </div>
      ) : null}

      {visible.length ? (
        <ul className="scroll-y max-h-[188px] divide-y divide-[#17171a] overflow-y-auto border-t border-line">
          {visible.map(({ item, index }) => (
            <li
              key={index}
              className={`flex cursor-pointer items-center justify-between gap-3 px-3 py-1.5 text-[12px] ${
                active === index ? "bg-[#111116]" : "hover:bg-[#0c0c0e]"
              }`}
              onMouseEnter={() => setHovered(index)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => setPinned(pinned === index ? null : index)}
            >
              <span className="flex min-w-0 items-center gap-2">
                <span
                  className="inline-block h-[8px] w-[8px] shrink-0 rounded-[2px]"
                  style={{ background: classColorCss(item.class_id) }}
                  aria-hidden
                />
                <span className="truncate text-ink">{item.class_name}</span>
                <span className="mono text-[10.5px] text-ink-3">#{item.class_id}</span>
                {item.mask ? (
                  <span className="text-[10.5px] text-ink-3">
                    {drawable(item.mask) ? "mask" : "mask (not stored)"}
                    {item.mask.area ? ` · ${fmt.int(Math.round(item.mask.area))} px` : ""}
                  </span>
                ) : null}
                {item.keypoints?.length ? (
                  <span className="text-[10.5px] text-ink-3">{item.keypoints.length} kp</span>
                ) : null}
              </span>
              <span className="num shrink-0 text-ink-2">{item.score.toFixed(3)}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {result?.classifications?.length ? (
        <ul className="divide-y divide-[#17171a] border-t border-line">
          {result.classifications.map((item, index) => (
            <li key={index} className="flex items-center gap-3 px-3 py-1.5 text-[12px]">
              <span className="w-44 truncate text-ink">{item.class_name}</span>
              <span className="h-[6px] flex-1 rounded-[2px] bg-[#141418]">
                <span
                  className="block h-[6px] rounded-[2px]"
                  style={{
                    width: `${Math.max(1, item.score * 100)}%`,
                    background: index === 0 ? "var(--color-series-1)" : "var(--color-line-2)",
                  }}
                />
              </span>
              <span className="num w-14 text-right text-ink-2">{item.score.toFixed(3)}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {result?.texts?.length ? (
        <ul className="divide-y divide-[#17171a] border-t border-line">
          {result.texts.map((item, index) => (
            <li key={index} className="flex items-center justify-between gap-3 px-3 py-1.5 text-[12px]">
              <span className="mono truncate text-ink">{item.text || "(empty)"}</span>
              <span className="num text-ink-2">{item.score.toFixed(3)}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {result?.embedding_dim ? (
        <div className="border-t border-line px-3 py-2.5">
          <p className="eyebrow mb-1.5">Embedding · {result.embedding_dim} dimensions</p>
          <EmbeddingStrip values={result.embedding ?? []} />
        </div>
      ) : null}

      {!instances.length && !result?.classifications?.length && !result?.texts?.length
        && !result?.embedding_dim ? (
        <p className="border-t border-line px-3 py-3 text-[12px] leading-relaxed text-ink-3">
          Nothing crossed the threshold on this image. Lower the score in the panel on the left and
          run it again, or check the decoder in Config.
        </p>
      ) : null}
    </div>
  );
}

/** An embedding has no picture; the shape of its values is still worth seeing. */
function EmbeddingStrip({ values }: { values: number[] }) {
  const slice = values.slice(0, 96);
  const max = Math.max(...slice.map(Math.abs), 1e-6);
  return (
    <div className="flex h-[34px] items-center gap-[1px]">
      {slice.map((value, index) => (
        <span
          key={index}
          title={value.toFixed(5)}
          style={{
            height: `${Math.max(2, (Math.abs(value) / max) * 100)}%`,
            background: value >= 0 ? "var(--color-series-1)" : "var(--color-series-2)",
            width: 3,
          }}
          className="rounded-[1px]"
        />
      ))}
      {values.length > slice.length ? (
        <span className="ml-2 text-[10.5px] text-ink-3">first {slice.length} of {values.length}</span>
      ) : null}
    </div>
  );
}
