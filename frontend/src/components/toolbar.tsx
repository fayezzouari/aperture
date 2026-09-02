import type { ReactNode } from "react";

/**
 * The filter bar that sits inside a panel, above the data it filters. It exists
 * so every list page has the same one — Models used to float its filters on the
 * page background while Inferences kept them in the panel, which read as two
 * different products.
 */
export function Toolbar({ children, trailing }: { children: ReactNode; trailing?: ReactNode }) {
  return (
    <div className="toolbar">
      {children}
      {trailing ? <div className="ml-auto flex items-center gap-2">{trailing}</div> : null}
    </div>
  );
}

/** Pick one of a few. Used for every time range and mode switch in the app. */
export function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** The window every page offers, so "24 h" means the same thing everywhere. */
export const TIME_WINDOWS = [
  { value: 1, label: "1 h" },
  { value: 24, label: "24 h" },
  { value: 168, label: "7 d" },
  { value: 720, label: "30 d" },
] as const;
