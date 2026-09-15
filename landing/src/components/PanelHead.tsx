import type { ReactNode } from "react";

/** The console's panel header: title, optional one-line subtitle, actions on the right. */
export function PanelHead({ title, subtitle, action }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <div className="panel-head">
      <div style={{ minWidth: 0 }}>
        <h3>{title}</h3>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {action ? <div className="action">{action}</div> : null}
    </div>
  );
}
