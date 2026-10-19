import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { can, useInstanceRole, type Workspace } from "../lib/hooks";
import { slugify } from "../lib/slug";
import { Button, ErrorNote, Field, Modal } from "./ui";

/** Matches the pattern the API enforces for workspace slugs. */
const WORKSPACE_SLUG = /^[a-z0-9][a-z0-9-]{1,62}$/;

/** The sidebar's workspace menu: every workspace the user can open, then "+ Add new workspace". */
export function WorkspaceSwitcher({
  workspaces,
  active,
  onSwitch,
}: {
  workspaces: Workspace[];
  active: Workspace | undefined;
  onSwitch: (slug: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const canCreate = can(useInstanceRole(), "developer");
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className="relative" ref={box}>
      <button
        className="flex h-8 w-full items-center gap-2 rounded-md border border-line px-2.5 text-left text-[12.5px] text-ink hover:bg-[#111113]"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Active workspace"
      >
        <span className="min-w-0 flex-1 truncate">{active?.name ?? "Choose a workspace"}</span>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="shrink-0 text-ink-3">
          <path d="M2 4l3 3 3-3" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />
        </svg>
      </button>

      {open ? (
        <div className="panel absolute left-0 top-9 z-50 w-full bg-raised p-1 shadow-xl" role="listbox" aria-label="Workspaces">
          <div className="scroll-y max-h-[280px] overflow-y-auto">
            {workspaces.map((workspace) => {
              const on = workspace.slug === active?.slug;
              return (
                <button
                  key={workspace.slug}
                  role="option"
                  aria-selected={on}
                  className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[12.5px] hover:bg-[#1a1a1e] hover:text-ink ${on ? "text-ink" : "text-ink-2"}`}
                  onClick={() => {
                    setOpen(false);
                    onSwitch(workspace.slug);
                  }}
                >
                  <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
                  {on ? <span aria-hidden className="text-[11px] text-ink-3">✓</span> : null}
                </button>
              );
            })}
          </div>
          {canCreate ? (
            <>
              {workspaces.length ? <div className="my-1 border-t border-line" /> : null}
              <button
                className="w-full rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-ink-2 hover:bg-[#1a1a1e] hover:text-ink"
                onClick={() => {
                  setOpen(false);
                  setCreating(true);
                }}
              >
                + Add new workspace
              </button>
            </>
          ) : null}
        </div>
      ) : null}

      <NewWorkspaceModal open={creating} onClose={() => setCreating(false)} onCreated={onSwitch} />
    </div>
  );
}

/** Name in, slug derived; the new workspace becomes the active one. */
export function NewWorkspaceModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (slug: string) => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ name: "", slug: "", description: "" });
  const [slugEdited, setSlugEdited] = useState(false);

  useEffect(() => {
    if (open) {
      setForm({ name: "", slug: "", description: "" });
      setSlugEdited(false);
    }
  }, [open]);

  const create = useMutation({
    mutationFn: () => api<Workspace>("/v1/workspaces", { method: "POST", body: form }),
    onSuccess: async (workspace) => {
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      onClose();
      onCreated(workspace.slug);
    },
  });

  const problem = form.slug && !WORKSPACE_SLUG.test(form.slug) ? "Use lowercase letters, digits and dashes, at least two characters." : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New workspace"
      subtitle="A workspace keeps its own models, workflows and members. You become its admin."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={create.isPending} disabled={!form.name.trim() || !form.slug || Boolean(problem)} onClick={() => create.mutate()}>
            Create and switch
          </Button>
        </>
      }
    >
      <div className="space-y-3.5">
        <Field label="Name">
          <input
            className="input"
            autoFocus
            value={form.name}
            placeholder="Research"
            onChange={(event) => {
              const name = event.target.value;
              setForm({ ...form, name, slug: slugEdited ? form.slug : slugify(name).slice(0, 63) });
            }}
          />
        </Field>
        <Field label="Slug" hint={problem ?? "What scripts and API calls name it by, in the X-Aperture-Workspace header. It cannot change later."}>
          <input
            className="input font-mono"
            value={form.slug}
            onChange={(event) => {
              setSlugEdited(true);
              setForm({ ...form, slug: event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 63) });
            }}
          />
        </Field>
        <Field label="Description">
          <textarea className="input min-h-[64px]" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
        </Field>
        {create.isError ? <ErrorNote error={create.error} /> : null}
      </div>
    </Modal>
  );
}

/** What a user with no workspace sees in place of the workspace pages. */
export function FirstWorkspace({ onCreated }: { onCreated: (slug: string) => void }) {
  const canCreate = can(useInstanceRole(), "developer");
  const [creating, setCreating] = useState(false);

  return (
    <div className="panel mx-auto mt-[10vh] max-w-md px-6 py-10 text-center">
      <p className="text-[15px] font-semibold text-ink">{canCreate ? "Create your first workspace" : "No workspace yet"}</p>
      <p className="mt-2 text-[12.5px] leading-relaxed text-ink-3">
        {canCreate
          ? "Models, workflows and their traffic live in a workspace. Make one for each context you run - a product, a team, an environment - and switch between them from the sidebar."
          : "You are not a member of any workspace. Ask a workspace admin to add you."}
      </p>
      {canCreate ? (
        <Button variant="primary" className="mt-5" onClick={() => setCreating(true)}>
          + Add new workspace
        </Button>
      ) : null}
      <NewWorkspaceModal open={creating} onClose={() => setCreating(false)} onCreated={onCreated} />
    </div>
  );
}
