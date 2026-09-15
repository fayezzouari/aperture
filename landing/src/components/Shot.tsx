/** A console screenshot. All images are 1440×900 at 2× and live in docs/screenshots. */
export const shots = {
  overview: "/screenshots/overview.png",
  models: "/screenshots/models.png",
  modelDetail: "/screenshots/model-detail.png",
  workflows: "/screenshots/workflows.png",
  workflowEditor: "/screenshots/workflow-editor.png",
  jobs: "/screenshots/jobs.png",
  fleet: "/screenshots/fleet.png",
  keys: "/screenshots/keys.png",
  audit: "/screenshots/audit.png",
} as const;

export type ShotKey = keyof typeof shots;
