import { useQuery } from "@tanstack/react-query";
import { api, session } from "./api";

export type Project = { id: string; slug: string; name: string; description: string; role?: string | null; model_count: number; capture_sample_rate: number | null; capture_retention_days: number | null };
export type Me = { id: string; email: string; name: string; is_superadmin: boolean; roles: Record<string, string>; provider: string };

export function useMe() {
  return useQuery<Me>({ queryKey: ["me"], queryFn: () => api("/v1/auth/me"), enabled: Boolean(session.token), staleTime: 60_000 });
}

export function useProjects() {
  return useQuery<Project[]>({ queryKey: ["projects"], queryFn: () => api("/v1/projects"), enabled: Boolean(session.token), staleTime: 60_000 });
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

/** The role the signed-in user holds on the active project. */
export function useRole(projectId?: string) {
  const me = useMe();
  if (!me.data) return "viewer";
  if (me.data.is_superadmin) return "admin";
  return (projectId && me.data.roles[projectId]) || "viewer";
}

export function can(role: string, minimum: "viewer" | "developer" | "admin") {
  const order = { viewer: 0, developer: 1, admin: 2 };
  return order[role as keyof typeof order] >= order[minimum];
}
