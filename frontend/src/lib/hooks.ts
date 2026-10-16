import { useQuery } from "@tanstack/react-query";
import { api, session } from "./api";

export type Role = "viewer" | "developer" | "admin";
export type Me = { id: string; email: string; name: string; role: Role; provider: string; is_active: boolean; last_login_at: string | null };

export function useMe() {
  return useQuery<Me>({ queryKey: ["me"], queryFn: () => api("/v1/auth/me"), enabled: Boolean(session.token), staleTime: 60_000 });
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

/** The role the signed-in user holds on this instance. */
export function useRole(): Role {
  return useMe().data?.role ?? "viewer";
}

export function can(role: string, minimum: "viewer" | "developer" | "admin") {
  const order = { viewer: 0, developer: 1, admin: 2 };
  return order[role as keyof typeof order] >= order[minimum];
}
