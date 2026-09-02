import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, session } from "../lib/api";
import { Button, ErrorNote, Field } from "../components/ui";

export default function Login() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const methods = useQuery<{ password: boolean; oidc: boolean; ldap: boolean; instance_name: string }>({
    queryKey: ["auth-methods"],
    queryFn: () => api("/v1/auth/methods"),
    retry: false,
  });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ access_token: string }>("/v1/auth/login", { body: { email, password } });
      session.token = result.access_token;
      navigate(params.get("next") || "/", { replace: true });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function ssoLogin() {
    try {
      const result = await api<{ authorize_url: string }>("/v1/auth/oidc/start");
      window.location.href = result.authorize_url;
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-[360px]">
        <div className="mb-7 flex items-center gap-2.5">
          <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden>
            <circle cx="16" cy="16" r="10" fill="none" stroke="#ededed" strokeWidth="2" />
            <path d="M16 6v20M7.1 11l17.8 10M7.1 21l17.8-10" stroke="#ededed" strokeWidth="1.1" opacity="0.5" />
          </svg>
          <div>
            <h1 className="text-[15px] font-semibold tracking-tight">{methods.data?.instance_name ?? "Aperture"}</h1>
            <p className="text-[11.5px] text-ink-3">Computer-vision model serving</p>
          </div>
        </div>

        <form onSubmit={submit} className="panel space-y-3.5 px-4 py-4">
          <Field label="Email" htmlFor="email">
            <input
              id="email"
              className="input"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              autoFocus
            />
          </Field>
          <Field label="Password" htmlFor="password">
            <input
              id="password"
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </Field>
          <ErrorNote error={error} />
          <Button type="submit" variant="primary" className="w-full" loading={busy}>
            Sign in
          </Button>

          {methods.data?.oidc ? (
            <>
              <div className="flex items-center gap-3 py-1">
                <span className="h-px flex-1 bg-line" />
                <span className="text-[11px] text-ink-3">or</span>
                <span className="h-px flex-1 bg-line" />
              </div>
              <Button type="button" className="w-full" onClick={ssoLogin}>
                Continue with SSO
              </Button>
            </>
          ) : null}
        </form>

        {methods.data?.ldap ? (
          <p className="mt-3 text-[11.5px] leading-relaxed text-ink-3">Directory accounts sign in with the same form — use your LDAP username as the email.</p>
        ) : null}
      </div>
    </div>
  );
}
