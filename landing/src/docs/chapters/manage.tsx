import { Code, FieldName, Note, Role, Rows, Shot, Steps, Ui, type Chapter } from "../kit";

export const keys: Chapter = {
  id: "keys",
  group: "Manage",
  title: "API keys",
  lede: (
    <p>
      Products authenticate with an API key, not a user session. A key belongs to one workspace and reaches only inference on that
      workspace's models - predict and run a workflow, nothing else. Issue one per consumer so each can be rate limited and revoked on its
      own.
    </p>
  ),
  sections: [
    {
      id: "keys-list",
      title: "The key list",
      body: (
        <>
          <p>
            <Ui>API keys</Ui> shows the active workspace's keys: prefix, scope, rate limit, this month's usage against its quota, when it was
            last used and when it expires. Below it, ready-to-copy calls in curl, Python and TypeScript.
          </p>
          <Shot name="keys" path="/keys" alt="The API keys of the e2e workspace" />
        </>
      ),
    },
    {
      id: "keys-create",
      title: "Issue a key",
      body: (
        <>
          <p>
            <Role min="admin" /> Workspace admins issue and revoke the workspace's keys.
          </p>
          <Steps>
            <li>
              Click <Ui>New key</Ui> and name it after the consuming product in <FieldName>Name</FieldName>.
            </li>
            <li>
              Untick <FieldName>Every model</FieldName> and tick only the models that product calls. A key for every model is convenient, but a
              leaked one then reaches all of them.
            </li>
            <li>
              Set the <FieldName>Rate limit / min</FieldName>, a <FieldName>Monthly quota</FieldName> and <FieldName>Expires in days</FieldName>{" "}
              (0 or blank means none). Click <Ui>Create key</Ui>.
            </li>
            <li>
              Copy the key from the next dialog and click <Ui>Done</Ui>. Aperture stores only a hash of it; a lost key cannot be shown again,
              only replaced.
            </li>
          </Steps>
          <Shot name="key-new" path="/keys" alt="The New API key dialog, scoped to one model" />
          <Shot name="key-created" path="/keys" alt="Copy this key now: the key is shown once" caption="Blurred here; in the console the key is readable until you click Done." />
          <p>
            <Ui>Revoke</Ui> on a key stops it immediately - callers get <code>api_key_revoked</code> on their next request. There is no
            confirmation step.
          </p>
        </>
      ),
    },
    {
      id: "keys-use",
      title: "Use a key",
      body: (
        <>
          <p>
            Send it in the <code>x-api-key</code> header. A key needs no workspace header - it always acts in its own workspace - and naming a
            different workspace is refused with <code>403</code>.
          </p>
          <Code title="Python and TypeScript SDKs">{`# python
from aperture import Aperture
client = Aperture("http://localhost:8080", api_key=os.environ["APERTURE_KEY"])
result = client.predict("e2e-part-detection", "production", images=["photo.jpg"])

// typescript
const client = new Aperture({ baseUrl: "http://localhost:8080", apiKey: process.env.APERTURE_KEY });
const result = await client.predict("e2e-part-detection", "production", { images: [file] });`}</Code>
          <Rows
            items={[
              ["401 api_key_invalid", "Unknown key, or a token sent in x-api-key."],
              ["401 api_key_revoked", "The key was revoked."],
              ["403 key_not_scoped_to_model", "The model is outside the key's scope."],
              ["429 rate_limited", "Over the per-minute limit; retry after the time in the error details."],
            ]}
          />
        </>
      ),
    },
  ],
};

export const tokens: Chapter = {
  id: "tokens",
  group: "Manage",
  title: "Personal access tokens and agents",
  lede: (
    <p>
      A personal access token is you, without a browser: it carries your roles in every workspace and reaches everything they allow - the
      registry, metrics, logs. Use one per script or agent so each can be revoked on its own. A token cannot create further tokens.
    </p>
  ),
  sections: [
    {
      id: "tokens-create",
      title: "Create a token",
      body: (
        <>
          <Steps>
            <li>
              Open your account menu and click <Ui>Personal access tokens</Ui>.
            </li>
            <li>
              Click <Ui>New token</Ui>, name it after where it will live in <FieldName>Name</FieldName>, and pick when it{" "}
              <FieldName>Expires</FieldName> - 30 days, 90 days, a year or never.
            </li>
            <li>
              Click <Ui>Create token</Ui> and copy it. The dialog also shows the exact command to connect Claude Code with it.
            </li>
          </Steps>
          <Shot name="tokens" path="/account/tokens" alt="Personal access tokens and how to connect an agent" />
          <Shot name="token-new" path="/account/tokens" alt="The New personal access token dialog" />
          <p>
            A token acts in the workspace you used last unless the request names one in <code>X-Aperture-Workspace</code>. Audit entries made
            with a token are marked <code>user:token</code>.
          </p>
        </>
      ),
    },
    {
      id: "tokens-mcp",
      title: "Connect Claude Code, Claude Desktop or Cursor",
      body: (
        <>
          <p>
            The MCP server in <code>mcp/</code> turns the API into tools: list models, read a model card, look at traffic and failures, run{" "}
            <code>predict</code> and see the overlay. It runs on your machine and is bounded by your roles.
          </p>
          <Code title="Claude Code">{`claude mcp add aperture \\
  -e APERTURE_URL=http://localhost:8080 \\
  -e APERTURE_TOKEN=ap_pat_xxxx.yyyy \\
  -e APERTURE_WORKSPACE=e2e \\
  -- uvx --from /path/to/aperture/mcp aperture-mcp`}</Code>
          <p>
            <code>APERTURE_WORKSPACE</code> is optional; every tool also takes <code>workspace=</code>, and <code>list_workspaces</code> shows
            the slugs.
          </p>
        </>
      ),
    },
  ],
};

export const admin: Chapter = {
  id: "admin",
  group: "Manage",
  title: "Audit and instance settings",
  lede: <p>Instance-wide administration: the audit trail, users, capture and retention, and the limits the instance runs with.</p>,
  sections: [
    {
      id: "admin-audit",
      title: "Audit log",
      body: (
        <>
          <p>
            Every mutating action is recorded, append-only: uploads, publishes, retags, deploys, keys and tokens, member changes, purges,
            sign-ins. Filter by time window, action or actor, and click a row to see its full detail.
          </p>
          <Shot name="audit" path="/audit" alt="The audit log for the last 24 hours" />
        </>
      ),
    },
    {
      id: "admin-users",
      title: "Users",
      body: (
        <>
          <p>
            <Role min="admin" scope="instance" /> <Ui>Settings</Ui> › <Ui>Users</Ui> lists every account with its provider, instance role and
            last sign-in.
          </p>
          <Steps>
            <li>
              To invite someone, type their <FieldName>Email</FieldName>, pick an instance <FieldName>Role</FieldName> and click{" "}
              <Ui>Invite</Ui>. With SSO or LDAP, this sets their role before their first sign-in.
            </li>
            <li>Change a role with the select in their row.</li>
            <li>
              <Ui>Deactivate</Ui> blocks a user and every token they issued; <Ui>Reactivate</Ui> undoes it. You cannot demote or deactivate
              yourself.
            </li>
          </Steps>
          <Shot name="settings-users" path="/settings" alt="Settings, Users tab: accounts and the invite form" />
          <Note>Inviting a user does not add them to a workspace. Add them as a member under Settings › Workspace.</Note>
        </>
      ),
    },
    {
      id: "admin-capture",
      title: "Capture and retention",
      body: (
        <>
          <p>
            Captured images power the explorer and retraining exports. Failed requests are always captured; successful ones are sampled.
          </p>
          <Steps>
            <li>
              <Role min="admin" scope="instance" /> Set the <FieldName>Sample rate</FieldName> - 5% is enough to spot a pattern and keeps
              storage sane.
            </li>
            <li>
              Set <FieldName>Retention (days)</FieldName>. After it, captured images are hard-deleted; flagged records are kept so a
              retraining set is not lost. Click <Ui>Save</Ui>.
            </li>
            <li>
              <Role min="admin" /> <Ui>Purge expired in this workspace</Ui> deletes the active workspace's expired captures now instead of
              waiting for the hourly sweep.
            </li>
          </Steps>
          <Shot name="settings-capture" path="/settings" alt="Settings, Capture and retention: sample rate, retention and the retraining export" />
        </>
      ),
    },
    {
      id: "admin-instance",
      title: "Instance limits and features",
      body: (
        <>
          <p>
            <Ui>Settings</Ui> › <Ui>Instance</Ui> is read-only: payload and upload limits, the default capture policy, whether a Roboflow key is
            configured, and which features are on. They are set with environment variables on the API container.
          </p>
          <Shot name="settings-instance" path="/settings" alt="Settings, Instance tab: limits, Roboflow and features" />
        </>
      ),
    },
  ],
};
