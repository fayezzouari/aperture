import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { Badge, Button, CodeBlock, EmptyState, ErrorNote, Field, Modal, Panel, PanelHeader, Spinner, useToast } from "../components/ui";

type Token = {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
};

const EXPIRY_OPTIONS: { label: string; days: number | null }[] = [
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 },
  { label: "Never", days: null },
];

function agentConfig(token: string) {
  const url = `${window.location.origin}/mcp`;
  return {
    claudeCode: `claude mcp add --transport http aperture ${url} --header "Authorization: Bearer ${token}"`,
    json: JSON.stringify({ mcpServers: { aperture: { url, headers: { Authorization: `Bearer ${token}` } } } }, null, 2),
  };
}

export default function Tokens() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<(Token & { token: string }) | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);

  const tokens = useQuery<Token[]>({
    queryKey: ["tokens", showRevoked],
    queryFn: () => api("/v1/auth/tokens", { query: showRevoked ? { include_revoked: true } : undefined }),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api(`/v1/auth/tokens/${id}/revoke`, { method: "POST" }),
    onSuccess: () => {
      toast("Token revoked. Anything still using it now gets token_revoked.");
      queryClient.invalidateQueries({ queryKey: ["tokens"] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const snippets = agentConfig("$APERTURE_TOKEN");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">Personal access tokens</h1>
          <p className="mt-0.5 text-[12.5px] text-ink-3">
            A token is you: it carries your role and outlives a browser session. Use one per agent or script so each can be revoked on its own.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          New token
        </Button>
      </div>

      <Panel>
        <PanelHeader
          title="Your tokens"
          action={
            <label className="flex cursor-pointer items-center gap-2 text-[12px] text-ink-3">
              <input type="checkbox" checked={showRevoked} onChange={(event) => setShowRevoked(event.target.checked)} />
              show revoked
            </label>
          }
        />
        {tokens.isLoading ? (
          <div className="flex h-32 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : !tokens.data?.length ? (
          <EmptyState
            title="No personal access tokens"
            body="Create one to let Claude Code, Claude Desktop, Cursor or a script call Aperture as you. Unlike an API key, a token reaches the registry, metrics and logs - everything your role allows."
            action={
              <Button variant="primary" onClick={() => setCreating(true)}>
                New token
              </Button>
            }
          />
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Prefix</th>
                  <th>Created</th>
                  <th>Last used</th>
                  <th>Expires</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tokens.data.map((token) => (
                  <tr key={token.id} className={token.revoked_at ? "opacity-50" : ""}>
                    <td className="text-[12.5px] text-ink">{token.name}</td>
                    <td className="mono text-[11.5px] text-ink-3">{token.prefix}</td>
                    <td className="text-[11.5px] text-ink-3">{fmt.when(token.created_at)}</td>
                    <td className="text-[11.5px] text-ink-3">{token.last_used_at ? fmt.ago(token.last_used_at) : "never"}</td>
                    <td className="text-[11.5px] text-ink-3">
                      {token.expires_at ? (
                        new Date(token.expires_at) < new Date() ? (
                          <Badge kind="warn">expired {fmt.ago(token.expires_at)}</Badge>
                        ) : (
                          fmt.when(token.expires_at)
                        )
                      ) : (
                        "never"
                      )}
                    </td>
                    <td className="text-right">
                      {token.revoked_at ? (
                        <Badge kind="bad">revoked</Badge>
                      ) : (
                        <Button size="sm" variant="danger" onClick={() => revoke.mutate(token.id)}>
                          Revoke
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel>
        <PanelHeader
          title="Connect an agent"
          subtitle="This instance serves its API as MCP tools at /mcp. An agent connects with a token and can do what you can - nothing to install."
        />
        <div className="space-y-3 px-4 py-4">
          <p className="text-[12px] text-ink-3">Claude Code</p>
          <CodeBlock language="shell" code={snippets.claudeCode} />
          <p className="text-[12px] text-ink-3">Cursor and other clients configured with JSON</p>
          <CodeBlock language="json" code={snippets.json} />
          <p className="text-[12px] text-ink-3">
            The server runs here, so <span className="mono">predict</span> takes image URLs or base64 rather than file paths.
          </p>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line px-3 py-2.5">
            <div>
              <p className="text-[12.5px] text-ink">Claude Desktop</p>
              <p className="text-[12px] text-ink-3">
                Install the extension, then enter <span className="mono">{window.location.origin}</span> and a token. It runs on your computer, so <span className="mono">predict</span> can read local files too.
              </p>
            </div>
            <a className="btn btn-sm" href="/downloads/aperture.mcpb" download>
              Download aperture.mcpb
            </a>
          </div>
          <p className="text-[12px] text-ink-3">
            Or call the API directly with <span className="mono">Authorization: Bearer $APERTURE_TOKEN</span>. A token cannot create further tokens; sign in here for that.
          </p>
        </div>
      </Panel>

      <CreateToken
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(token) => {
          setCreated(token);
          queryClient.invalidateQueries({ queryKey: ["tokens"] });
        }}
      />

      <Modal
        open={Boolean(created)}
        onClose={() => setCreated(null)}
        title="Copy this token now"
        subtitle="Aperture stores only a hash of it. If it is lost, revoke it and issue a new one."
        wide
        footer={
          <Button variant="primary" onClick={() => setCreated(null)}>
            Done
          </Button>
        }
      >
        {created ? (
          <div className="space-y-3">
            <CodeBlock language="key" code={created.token} />
            <p className="text-[12px] text-ink-3">Ready to paste into Claude Code. For Claude Desktop, paste the token into the extension.</p>
            <CodeBlock language="shell" code={agentConfig(created.token).claudeCode} />
          </div>
        ) : null}
      </Modal>
    </div>
  );
}

function CreateToken({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (token: Token & { token: string }) => void }) {
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState<number | null>(90);

  const create = useMutation({
    mutationFn: () => api<Token & { token: string }>("/v1/auth/tokens", { body: { name, expires_in_days: expiry } }),
    onSuccess: (token) => {
      onCreated(token);
      onClose();
      setName("");
      setExpiry(90);
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New personal access token"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>
            Create token
          </Button>
        </>
      }
    >
      <div className="space-y-3.5">
        <Field label="Name" hint="Where it will live: the machine or the agent.">
          <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="claude-code on my laptop" autoFocus />
        </Field>
        <Field label="Expires" hint="Shorter is safer; a token can always be reissued.">
          <div className="flex flex-wrap gap-1.5">
            {EXPIRY_OPTIONS.map((option) => (
              <Button key={option.label} size="sm" variant={expiry === option.days ? "accent" : "default"} onClick={() => setExpiry(option.days)}>
                {option.label}
              </Button>
            ))}
          </div>
        </Field>
        <ErrorNote error={create.error} />
      </div>
    </Modal>
  );
}
