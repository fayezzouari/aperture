import { useQuery } from "@tanstack/react-query";
import { api, session } from "./api";

export type Role = "viewer" | "developer" | "admin";
export type Me = { id: string; email: string; name: string; role: Role; provider: string; is_active: boolean; last_login_at: string | null; last_workspace_id: string | null };
export type Workspace = { id: string; slug: string; name: string; description: string; created_at: string; role: Role; model_count: number; member_count: number };

export function useMe() {
  return useQuery<Me>({ queryKey: ["me"], queryFn: () => api("/v1/auth/me"), enabled: Boolean(session.token), staleTime: 60_000 });
}

export function useWorkspaces() {
  return useQuery<Workspace[]>({ queryKey: ["workspaces"], queryFn: () => api("/v1/workspaces"), enabled: Boolean(session.token), staleTime: 60_000 });
}

/** The workspace every page is looking at; the shell picks it before any page renders. */
export function useWorkspace(): Workspace | undefined {
  return useWorkspaces().data?.find((workspace) => workspace.slug === session.workspace);
}

export function useInstance() {
  return useQuery<{
    instance_name: string;
    env: string;
    version: string;
    limits: { max_payload_mb: number; max_batch_images: number; max_upload_mb: number; upload_chunk_mb: number };
    capture: { default_sample_rate: number; retention_days: number };
    features: Record<string, boolean>;
  }>({ queryKey: ["instance"], queryFn: () => api("/v1/meta/instance"), staleTime: 300_000 });
}

/** The role the signed-in user holds in the active workspace: what models and workflows allow. */
export function useRole(): Role {
  return useWorkspace()?.role ?? "viewer";
}

/** The instance role: what keys, users, alerts, reports and settings allow. */
export function useInstanceRole(): Role {
  return useMe().data?.role ?? "viewer";
}

export function can(role: string, minimum: "viewer" | "developer" | "admin") {
  const order = { viewer: 0, developer: 1, admin: 2 };
  return order[role as keyof typeof order] >= order[minimum];
}
