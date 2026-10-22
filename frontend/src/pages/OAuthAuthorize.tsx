import { useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Button, ErrorNote, Spinner } from "../components/ui";

type ClientView = {
  client_id: string;
  client_name: string;
  client_uri: string | null;
  redirect_uri: string;
  redirect_host: string;
  user_email: string;
};

// the OAuth request as the client sent it; the API validates every field again on submit
const FIELDS = ["client_id", "redirect_uri", "response_type", "code_challenge", "code_challenge_method", "state", "resource", "scope"];

/**
 * The consent page an MCP client sends a person to. It names the application and
 * where the answer goes, and approving hands that application a token that is
 * this person - so the page says so, plainly, before anything is issued.
 */
export default function OAuthAuthorize() {
  const location = useLocation();
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState<unknown>(null);

  const request = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const out: Record<string, string> = {};
    for (const field of FIELDS) {
      const value = params.get(field);
      if (value) out[field] = value;
    }
    return out;
  }, [location.search]);

  const client = useQuery<ClientView>({
    queryKey: ["oauth-client", location.search],
    queryFn: () => api("/v1/oauth/authorize", { query: request }),
    retry: false,
  });

  async function decide(approve: boolean) {
    setBusy(approve ? "approve" : "deny");
    setError(null);
    try {
      const result = await api<{ redirect_to: string }>("/v1/oauth/authorize", { body: { ...request, approve } });
      window.location.assign(result.redirect_to);
    } catch (caught) {
      setError(caught);
      setBusy(null);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-[420px]">
        <div className="mb-6 flex items-center gap-2.5">
          <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden>
            <circle cx="16" cy="16" r="10" fill="none" stroke="#ededed" strokeWidth="2" />
            <path d="M16 6v20M7.1 11l17.8 10M7.1 21l17.8-10" stroke="#ededed" strokeWidth="1.1" opacity="0.5" />
          </svg>
          <h1 className="text-[15px] font-semibold tracking-tight">Connect an application</h1>
        </div>

        <div className="panel space-y-4 px-4 py-4">
          {client.isLoading ? (
            <div className="flex h-24 items-center justify-center text-ink-3">
              <Spinner size={18} />
            </div>
          ) : client.data ? (
            <>
              <div className="space-y-1.5">
                <p className="text-[14px] text-ink">
                  <span className="font-semibold">{client.data.client_name}</span> wants to use Aperture as you.
                </p>
                <p className="text-[12.5px] leading-relaxed text-ink-3">
                  It will be able to do everything <span className="text-ink-2">{client.data.user_email}</span> can: read models, traffic and logs,
                  run predictions, and change what your roles allow it to change. Its actions are recorded in the audit log under your name.
                </p>
              </div>
              <dl className="space-y-1.5 rounded-lg border border-line px-3 py-2.5 text-[12px]">
                <div className="flex justify-between gap-3">
                  <dt className="text-ink-3">Sends the answer to</dt>
                  <dd className="mono truncate text-ink-2" title={client.data.redirect_uri}>
                    {client.data.redirect_host}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-ink-3">Access lasts</dt>
                  <dd className="text-ink-2">30 days from its last use, until you revoke it</dd>
                </div>
              </dl>
              <p className="text-[12px] text-ink-3">
                Only approve an application you just started connecting yourself. You can revoke it any time under <span className="text-ink-2">Personal access tokens</span>.
              </p>
              <ErrorNote error={error} />
              <div className="flex gap-2">
                <Button className="flex-1" onClick={() => decide(false)} loading={busy === "deny"} disabled={busy !== null}>
                  Deny
                </Button>
                <Button variant="primary" className="flex-1" onClick={() => decide(true)} loading={busy === "approve"} disabled={busy !== null}>
                  Approve
                </Button>
              </div>
            </>
          ) : (
            <>
              <p className="text-[13px] text-ink">This connection request cannot be completed.</p>
              <ErrorNote error={client.error} />
              <p className="text-[12px] text-ink-3">Start connecting again from the application.</p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
