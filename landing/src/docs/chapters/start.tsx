import { Code, FieldName, Note, Role, Rows, Shot, Steps, Ui, type Chapter } from "../kit";

export const install: Chapter = {
  id: "install",
  group: "Getting started",
  title: "Install and upgrade",
  lede: (
    <p>
      Aperture runs on your own hardware with Docker Compose: an API, a pool of runtimes that hold the weights, a worker, a log sink, and
      Postgres, MinIO and Redis behind them. Nothing calls out to the internet at runtime.
    </p>
  ),
  sections: [
    {
      id: "install-run",
      title: "Run it",
      body: (
        <>
          <Steps>
            <li>
              Clone the repository and copy the environment file: <code>cp .env.example .env</code>.
            </li>
            <li>
              Change the three values marked <code>CHANGE ME</code>: <code>AP_JWT_SECRET</code>, <code>AP_RUNTIME_INTERNAL_TOKEN</code> and
              the bootstrap admin's <code>AP_BOOTSTRAP_ADMIN_EMAIL</code> / <code>AP_BOOTSTRAP_ADMIN_PASSWORD</code>.
            </li>
            <li>
              Start the stack with <code>make up</code>. The API applies database migrations itself before it starts serving.
            </li>
            <li>
              Open <code>http://localhost:8080</code> and sign in as the bootstrap admin.
            </li>
          </Steps>
          <Code title="Quickstart">{`git clone <this repo> && cd aperture
cp .env.example .env          # change the CHANGE ME values
make up                       # build and start; also how you upgrade
make seed                     # optional: a demo workspace with traffic
make e2e                      # optional: the end-to-end check (builds the e2e workspace)
make seed-fruit               # optional: the real models the guide's screenshots show`}</Code>
        </>
      ),
    },
    {
      id: "install-upgrade",
      title: "Upgrade",
      body: (
        <>
          <p>
            Pull the new version and run <code>make up</code> again. The API container runs <code>alembic upgrade head</code> once before
            uvicorn starts its workers; a second API container starting at the same moment waits on a database lock and finds nothing left to
            do.
          </p>
          <Note title="Running migrations as a separate step">
            Set <code>AP_MIGRATE_ON_START=false</code> when your deploy pipeline migrates on its own, then run <code>make migrate</code> before
            you roll out the new image.
          </Note>
        </>
      ),
    },
    {
      id: "install-processes",
      title: "What runs where",
      body: (
        <Rows
          items={[
            ["api", "Registry, console backend and inference gateway in one stateless process. Scale it with replicas."],
            ["runtime ×N", "Holds model versions resident on CPU or GPU, batches requests across callers, reports what it holds every few seconds."],
            ["worker", "Imports, conversions, bulk and video jobs, exports, and the cron ticks that reconcile the fleet, evaluate alerts and expire captures."],
            ["logsink", "Drains the inference-log stream into Postgres, so a database hiccup slows the explorer, not serving."],
            ["stores", "Postgres for metadata, MinIO or S3 for weights and captured images, Redis for routing, tags, rate limits and the log stream."],
          ]}
        />
      ),
    },
  ],
};

export const basics: Chapter = {
  id: "basics",
  group: "Getting started",
  title: "Sign in and find your way",
  lede: <p>The console is one sidebar and one column. Everything it does goes through the same API your products and scripts call.</p>,
  sections: [
    {
      id: "basics-sign-in",
      title: "Sign in",
      body: (
        <>
          <Steps>
            <li>
              Open the console. Without a session you land on the sign-in page.
            </li>
            <li>
              Enter your <FieldName>Email</FieldName> and <FieldName>Password</FieldName> and click <Ui>Sign in</Ui>. With LDAP enabled, use
              your directory username as the email. With SSO enabled, click <Ui>Continue with SSO</Ui> instead.
            </li>
          </Steps>
          <Shot name="login" path="/login" alt="The sign-in page with email and password fields" />
        </>
      ),
    },
    {
      id: "basics-layout",
      title: "The sidebar",
      body: (
        <>
          <p>
            At the top is the <b>workspace switcher</b>. Below it the pages are grouped in three: <b>Build</b> (Overview, Models, Workflows),{" "}
            <b>Operate</b> (Inferences, Fleet, Jobs, Reports, Alerts) and <b>Manage</b> (API keys, Audit, Settings). At the bottom are the API
            docs, your account menu and the instance version.
          </p>
          <Rows
            items={[
              ["In the workspace", "Overview, Models, Workflows, Inferences, Jobs and API keys show the active workspace only, and change when you switch."],
              ["Instance-wide", "Fleet, Reports, Alerts, Audit, Settings and your tokens look the same in every workspace."],
            ]}
          />
          <Shot
            name="overview"
            path="/"
            alt="The overview of the fruit workspace: requests, error rate, latency, replicas, runtimes and recent failures"
            caption="The overview is titled with the workspace's name. It covers the last 24 hours and refreshes every 15 seconds."
          />
        </>
      ),
    },
    {
      id: "basics-account",
      title: "Your account",
      body: (
        <>
          <p>
            Click your email at the bottom of the sidebar. The menu shows your role in the active workspace (or <i>Instance admin</i>), a link to{" "}
            <Ui>Personal access tokens</Ui> and <Ui>Sign out</Ui>. Signing out clears the session in this browser only.
          </p>
          <Shot name="account-menu" path="/" alt="The account menu: role, personal access tokens and sign out" />
        </>
      ),
    },
  ],
};

export const workspaces: Chapter = {
  id: "workspaces",
  group: "Getting started",
  title: "Workspaces and roles",
  lede: (
    <>
      <p>
        A workspace keeps one context apart from another on the same instance - a <code>research</code> team and a <code>retail</code> one, a
        team, an environment. Each has its own models, workflows, API keys and members. Model and workflow slugs are unique inside a
        workspace, so two workspaces can both have a <code>detector</code> model.
      </p>
      <p>
        Everything that hangs off a model - versions, deployments, inference logs, drift, jobs - follows it into its workspace. Alerts,
        reports, the audit log, capture settings and the runtime fleet stay instance-wide.
      </p>
    </>
  ),
  sections: [
    {
      id: "workspaces-switch",
      title: "Switch workspace",
      body: (
        <>
          <Steps>
            <li>
              Click the workspace name at the top of the sidebar.
            </li>
            <li>
              Pick another workspace from the list. The active one has a check mark.
            </li>
          </Steps>
          <Shot name="workspace-switcher" path="/" alt="The workspace menu open, listing workspaces and + Add new workspace" />
          <p>
            The switch happens in place, without a reload: every page drops the previous workspace's data and loads the new one's. If you were
            on a model or workflow page, you go back to its list, since the new workspace may not have it. The console remembers your choice on
            the server, so your next sign-in - in any browser - opens on the same workspace.
          </p>
        </>
      ),
    },
    {
      id: "workspaces-create",
      title: "Create a workspace",
      body: (
        <>
          <p>
            <Role min="developer" scope="instance" /> Instance developers and admins can create workspaces; whoever creates one becomes its
            admin.
          </p>
          <Steps>
            <li>
              Open the workspace menu and click <Ui>+ Add new workspace</Ui>.
            </li>
            <li>
              Type a <FieldName>Name</FieldName>. The <FieldName>Slug</FieldName> follows it - lowercase letters, digits and dashes. The slug is
              what scripts send in the <code>X-Aperture-Workspace</code> header and it cannot change later.
            </li>
            <li>
              Optionally add a <FieldName>Description</FieldName>, then click <Ui>Create and switch</Ui>.
            </li>
          </Steps>
          <Shot name="workspace-new" path="/" alt="The New workspace dialog with name, slug and description" />
          <p>A fresh install has no workspace at all. Until someone creates one, the workspace pages show this instead:</p>
          <Shot name="workspace-first" path="/" alt="Create your first workspace: the screen a user with no workspace sees" />
          <Note>
            A user who is not a member of any workspace and cannot create one sees <i>No workspace yet</i> and is asked to contact a workspace
            admin.
          </Note>
        </>
      ),
    },
    {
      id: "workspaces-members",
      title: "Members and roles",
      body: (
        <>
          <p>
            Open <Ui>Settings</Ui> and the <Ui>Workspace</Ui> tab. Here you rename the workspace and manage who is in it. Each member has one
            role in this workspace:
          </p>
          <Rows
            items={[
              ["viewer", "Reads models, versions, traffic and workflows. Can flag predictions."],
              ["developer", "Also creates models and workflows, uploads, publishes, tags, deploys, runs the playground and exports."],
              ["admin", "Also manages members, API keys and capture purges for this workspace."],
            ]}
          />
          <Steps>
            <li>
              <Role min="admin" /> To add someone, type their <FieldName>Email</FieldName> (they need an account on the instance), choose a{" "}
              <FieldName>Role</FieldName> and click <Ui>Add member</Ui>.
            </li>
            <li>Change a member's role with the select in their row; it applies on their next request.</li>
            <li>
              Click <Ui>Remove</Ui> to take someone out. A workspace always keeps at least one admin.
            </li>
          </Steps>
          <Shot name="settings-workspace" path="/settings" alt="Settings, Workspace tab: name, description and the members table" />
          <Note title="Instance roles">
            Separately, every user has an instance role. An instance <b>admin</b> manages users and instance settings and is admin in every
            workspace without being listed; an instance <b>developer</b> can also create workspaces.
          </Note>
        </>
      ),
    },
  ],
};
