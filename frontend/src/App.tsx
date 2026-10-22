import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, session } from "./lib/api";
import { ActiveWorkspace, useInstance, useMe, useWorkspaces } from "./lib/hooks";
import { Mark } from "./components/Mark";
import { ErrorNote, Spinner } from "./components/ui";
import { FirstWorkspace, WorkspaceSwitcher } from "./components/workspaces";

const Login = lazy(() => import("./pages/Login"));
const Overview = lazy(() => import("./pages/Overview"));
const Models = lazy(() => import("./pages/Models"));
const Workflows = lazy(() => import("./pages/Workflows"));
const WorkflowEditor = lazy(() => import("./pages/WorkflowEditor"));
const ModelDetail = lazy(() => import("./pages/ModelDetail"));
const Traces = lazy(() => import("./pages/Traces"));
const Keys = lazy(() => import("./pages/Keys"));
const Reports = lazy(() => import("./pages/Reports"));
const Alerts = lazy(() => import("./pages/Alerts"));
const Jobs = lazy(() => import("./pages/Jobs"));
const Audit = lazy(() => import("./pages/Audit"));
const Fleet = lazy(() => import("./pages/Fleet"));
const Tokens = lazy(() => import("./pages/Tokens"));
const Settings = lazy(() => import("./pages/Settings"));

const NAV = [
  {
    group: "Build",
    items: [
      { to: "/", label: "Overview" },
      { to: "/models", label: "Models" },
      { to: "/workflows", label: "Workflows" },
    ],
  },
  {
    group: "Operate",
    items: [
      { to: "/traces", label: "Inferences" },
      { to: "/fleet", label: "Fleet" },
      { to: "/jobs", label: "Jobs" },
      { to: "/reports", label: "Reports" },
      { to: "/alerts", label: "Alerts" },
    ],
  },
  {
    group: "Manage",
    items: [
      { to: "/keys", label: "API keys" },
      { to: "/audit", label: "Audit" },
      { to: "/settings", label: "Settings" },
    ],
  },
];

// pages that show one workspace's models, workflows and traffic; the rest are instance-wide
const WORKSPACE_PAGES = ["/models", "/workflows", "/traces", "/jobs", "/keys"];
const inWorkspace = (path: string) => path === "/" || WORKSPACE_PAGES.some((prefix) => path.startsWith(prefix));

// queries that do not change with the workspace, kept across a switch
const INSTANCE_QUERIES = new Set(["me", "workspaces", "instance"]);

export default function App() {
  const location = useLocation();
  const authed = Boolean(session.token);

  if (!authed && location.pathname !== "/login") {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }

  return (
    <Suspense fallback={<FullPageSpinner />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="*"
          element={
            <Shell>
              <Routes>
                <Route path="/" element={<Overview />} />
                <Route path="/models" element={<Models />} />
                <Route path="/models/:slug" element={<ModelDetail />} />
                <Route path="/workflows" element={<Workflows />} />
                <Route path="/workflows/:slug" element={<WorkflowEditor />} />
                <Route path="/traces" element={<Traces />} />
                <Route path="/fleet" element={<Fleet />} />
                <Route path="/jobs" element={<Jobs />} />
                <Route path="/reports" element={<Reports />} />
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/keys" element={<Keys />} />
                <Route path="/audit" element={<Audit />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/account/tokens" element={<Tokens />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Shell>
          }
        />
      </Routes>
    </Suspense>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const me = useMe();
  const instance = useInstance();
  const workspaces = useWorkspaces();
  const [menuOpen, setMenuOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [active, setActive] = useState<string | null>(session.workspace);

  // open on the workspace this user switched to last, or the first one they can see
  useEffect(() => {
    const list = workspaces.data;
    if (!list || !me.data) return;
    if (active && list.some((workspace) => workspace.slug === active)) return;
    const remembered = list.find((workspace) => workspace.id === me.data.last_workspace_id);
    const pick = (remembered ?? list[0])?.slug ?? null;
    session.workspace = pick;
    setActive(pick);
  }, [workspaces.data, me.data, active]);

  const switchTo = useCallback(
    (slug: string) => {
      if (slug === session.workspace) return;
      session.workspace = slug;
      setActive(slug);
      // everything workspace-shaped is dropped, so no page shows the last workspace's rows
      queryClient.removeQueries({ predicate: (query) => !INSTANCE_QUERIES.has(String(query.queryKey[0])) });
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      // a model or workflow page names something the new workspace may not have
      if (/^\/(models|workflows)\/./.test(location.pathname)) navigate(`/${location.pathname.split("/")[1]}`);
      api(`/v1/workspaces/${slug}/activate`, { method: "POST" })
        .then(() => queryClient.invalidateQueries({ queryKey: ["me"] }))
        .catch(() => undefined); // remembering is a convenience; the switch already happened
    },
    [queryClient, navigate, location.pathname],
  );
  const current = workspaces.data?.find((workspace) => workspace.slug === active);

  // a route change closes the mobile drawer and the account menu
  useEffect(() => {
    setDrawerOpen(false);
    setMenuOpen(false);
  }, [location.pathname]);

  const role = me.data?.role === "admin" ? "Instance admin" : current ? `Role in ${current.name}: ${current.role}` : `Role: ${me.data?.role ?? "viewer"}`;

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex h-[52px] shrink-0 items-center gap-2.5 px-4">
        <Link to="/" className="flex items-center gap-2.5" aria-label="Aperture home">
          <Mark />
          <span className="text-[14px] font-semibold tracking-tight">Aperture</span>
        </Link>
        {instance.data && instance.data.env !== "production" ? (
          <span className="badge ml-auto">{instance.data.env}</span>
        ) : null}
      </div>

      <div className="px-3 pb-2">
        <WorkspaceSwitcher workspaces={workspaces.data ?? []} active={current} onSwitch={switchTo} />
      </div>

      <nav className="scroll-y flex-1 overflow-y-auto px-2 pb-4 pt-2" aria-label="Main">
        {NAV.map((section) => (
          <div key={section.group} className="mt-4 first:mt-0">
            <p className="px-2.5 pb-1.5 text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-2">{section.group}</p>
            <ul className="flex flex-col gap-px">
              {section.items.map((item) => {
                const on = item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to);
                return (
                  <li key={item.to}>
                    <Link
                      to={item.to}
                      aria-current={on ? "page" : undefined}
                      className={`block rounded-md px-2.5 py-1.5 text-[13px] transition-colors ${
                        on ? "bg-[#1a1a1e] text-ink" : "text-ink-3 hover:bg-[#111113] hover:text-ink-2"
                      }`}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="shrink-0 border-t border-line p-2">
        <a
          className="block rounded-md px-2.5 py-1.5 text-[12.5px] text-ink-3 hover:bg-[#111113] hover:text-ink-2"
          href="/api/docs"
          target="_blank"
          rel="noreferrer"
        >
          API docs
        </a>
        <div className="relative">
          <button
            className="w-full truncate rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-ink-2 hover:bg-[#111113] hover:text-ink"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
          >
            {me.data?.email ?? "Account"}
          </button>
          {menuOpen ? (
            <div className="panel absolute bottom-9 left-0 z-50 w-full bg-raised p-1 shadow-xl">
              <p className="px-2.5 py-1.5 text-[11.5px] text-ink-3">{role}</p>
              <Link
                to="/account/tokens"
                className="block w-full rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-ink-2 hover:bg-[#1a1a1e] hover:text-ink"
                onClick={() => setMenuOpen(false)}
              >
                Personal access tokens
              </Link>
              <button
                className="w-full rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-ink-2 hover:bg-[#1a1a1e] hover:text-ink"
                onClick={() => {
                  session.token = null;
                  session.workspace = null;
                  queryClient.clear();
                  navigate("/login");
                }}
              >
                Sign out
              </button>
            </div>
          ) : null}
        </div>
        <p className="px-2.5 pt-2 text-[10.5px] leading-snug text-ink-3">
          {instance.data?.instance_name ?? "Aperture"} {instance.data?.version ? `v${instance.data.version}` : ""} · self-hosted
        </p>
      </div>
    </div>
  );

  return (
    <ActiveWorkspace.Provider value={current ? active : null}>
      <div className="min-h-full">
        <aside className="fixed inset-y-0 left-0 z-40 hidden w-[232px] border-r border-line bg-black lg:block">{sidebar}</aside>

        {/* below lg the sidebar becomes a drawer behind a slim top bar */}
        <header className="sticky top-0 z-40 flex h-[52px] items-center gap-3 border-b border-line bg-black/85 px-4 backdrop-blur lg:hidden">
          <button className="btn btn-sm btn-ghost" onClick={() => setDrawerOpen(true)} aria-label="Open navigation" aria-expanded={drawerOpen}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
              <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          <Link to="/" className="flex items-center gap-2.5" aria-label="Aperture home">
            <Mark />
            <span className="text-[14px] font-semibold tracking-tight">Aperture</span>
          </Link>
        </header>
        {drawerOpen ? (
          <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true">
            <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setDrawerOpen(false)} />
            <aside className="absolute inset-y-0 left-0 w-[260px] max-w-[85vw] border-r border-line bg-black">{sidebar}</aside>
          </div>
        ) : null}

        <div className="lg:pl-[232px]">
          <main className="mx-auto max-w-[1440px] px-4 py-6 lg:px-6">
            {!inWorkspace(location.pathname) ? (
              children
            ) : workspaces.data && !workspaces.data.length ? (
              <FirstWorkspace onCreated={switchTo} />
            ) : active && current ? (
              // keyed so every page remounts, and refetches, in the workspace switched to
              <div key={active}>{children}</div>
            ) : workspaces.isError ? (
              <ErrorNote error={workspaces.error} />
            ) : (
              <div className="flex h-40 items-center justify-center text-ink-3">
                <Spinner size={18} />
              </div>
            )}
          </main>
        </div>
      </div>
    </ActiveWorkspace.Provider>
  );
}

function FullPageSpinner() {
  return (
    <div className="flex h-screen items-center justify-center text-ink-3">
      <Spinner size={20} />
    </div>
  );
}

function NotFound() {
  return (
    <div className="panel px-6 py-14 text-center">
      <p className="text-sm text-ink">That page does not exist.</p>
      <Link to="/" className="link mt-3 inline-block text-[12.5px]">
        Back to the overview
      </Link>
    </div>
  );
}
