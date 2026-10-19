/**
 * A console screenshot. The landing page shares the user guide's images: 1440×900
 * at 2×, taken in the e2e workspace by scripts/guide_screenshots.py.
 */
export const shots = {
  overview: "/screenshots/guide/overview.webp",
  models: "/screenshots/guide/models-list.webp",
  modelDetail: "/screenshots/guide/model-versions.webp",
  workflows: "/screenshots/guide/workflows-list.webp",
  workflowEditor: "/screenshots/guide/workflow-editor.webp",
  jobs: "/screenshots/guide/jobs.webp",
  fleet: "/screenshots/guide/fleet.webp",
  keys: "/screenshots/guide/keys.webp",
  audit: "/screenshots/guide/audit.webp",
} as const;

export type ShotKey = keyof typeof shots;
