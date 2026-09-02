import { Suspense, lazy, useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { session } from "./lib/api";
import { useInstance, useMe, useProjects } from "./lib/hooks";
import { Spinner } from "./components/ui";

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
const Settings = lazy(() => import("./pages/Settings"));

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/models", label: "Models" },
  { to: "/workflows", label: "Workflows" },
  { to: "/traces", label: "Inferences" },
  { to: "/fleet", label: "Fleet" },
  { to: "/jobs", label: "Jobs" },
  { to: "/reports", label: "Reports" },
  { to: "/alerts", label: "Alerts" },
  { to: "/keys", label: "API keys" },
  { to: "/audit", label: "Audit" },
  { to: "/settings", label: "Settings" },
];

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
  const projects = useProjects();
  const instance = useInstance();
  const [menuOpen, setMenuOpen] = useState(false);

  const active = session.project ?? projects.data?.[0]?.slug ?? null;
  useEffect(() => {
    if (!session.project && projects.data?.length) {
      session.project = projects.data[0].slug;
      queryClient.invalidateQueries();
    }
  }, [projects.data, queryClient]);

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-40 border-b border-line bg-black/85 backdrop-blur">
        <div className="mx-auto flex h-[52px] max-w-[1440px] items-center gap-3 px-4">
          <Link to="/" className="flex items-center gap-2.5" aria-label="Aperture home">
            <Mark />
            <span className="text-[14px] font-semibold tracking-tight">Aperture</span>
          </Link>

          <span className="text-ink-3">/</span>

          <select
            className="select h-7 w-auto min-w-[150px] border-line bg-transparent text-[12.5px]"
            value={active ?? ""}
            onChange={(event) => {
              session.project = event.target.value;
              queryClient.invalidateQueries();
              navigate("/");
            }}
            aria-label="Active project"
          >
            {(projects.data ?? []).map((project) => (
              <option key={project.slug} value={project.slug}>
                {project.name}
              </option>
            ))}
          </select>

          {instance.data && instance.data.env !== "production" ? (
            <span className="badge hidden sm:inline-flex">{instance.data.env}</span>
          ) : null}

          <div className="ml-auto flex items-center gap-2">
            <a className="btn btn-sm btn-ghost hidden sm:inline-flex" href="/api/docs" target="_blank" rel="noreferrer">
              API docs
            </a>
            <div className="relative">
              <button className="btn btn-sm btn-ghost" onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen}>
                {me.data?.email ?? "Account"}
              </button>
              {menuOpen ? (
                <div className="panel absolute right-0 top-9 z-50 w-56 bg-raised p-1 shadow-xl">
                  <p className="px-2.5 py-1.5 text-[11.5px] text-ink-3">
                    {me.data?.is_superadmin ? "Instance admin" : `Role: ${(active && me.data?.roles[projects.data?.find((p) => p.slug === active)?.id ?? ""]) ?? "viewer"}`}
                  </p>
                  <button
                    className="w-full rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-ink-2 hover:bg-[#1a1a1e] hover:text-ink"
                    onClick={() => {
                      session.token = null;
                      session.project = null;
                      queryClient.clear();
                      navigate("/login");
                    }}
                  >
                    Sign out
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <nav className="mx-auto max-w-[1440px] px-2">
          <ul className="nav-x flex gap-0.5">
            {NAV.map((item) => {
              const on = item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to);
              return (
                <li key={item.to}>
                  <Link
                    to={item.to}
                    className={`relative inline-block whitespace-nowrap px-2.5 py-2 text-[13px] transition-colors ${on ? "text-ink" : "text-ink-3 hover:text-ink-2"}`}
                  >
                    {item.label}
                    {on ? <span className="absolute inset-x-2 -bottom-px h-[1.5px] bg-ink" /> : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </header>

      <main className="mx-auto max-w-[1440px] px-4 py-6">{children}</main>

      <footer className="mx-auto max-w-[1440px] px-4 pb-8 pt-2">
        <p className="text-[11px] text-ink-3">
          {instance.data?.instance_name ?? "Aperture"} {instance.data?.version ? `v${instance.data.version}` : ""} · self-hosted · no outbound calls at runtime
        </p>
      </footer>
    </div>
  );
}

function Mark() {
  return (
    <svg width="20" height="20" viewBox="0 0 32 32" aria-hidden>
      <circle cx="16" cy="16" r="10" fill="none" stroke="#ededed" strokeWidth="2" />
      <path d="M16 6v20M7.1 11l17.8 10M7.1 21l17.8-10" stroke="#ededed" strokeWidth="1.1" opacity="0.5" />
    </svg>
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
