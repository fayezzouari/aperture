import type { ReactNode } from "react";

export function SectionHead({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="section-head">
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  );
}
