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
          <Shot name="keys" path="/keys" alt="The API keys of the fruit workspace" />
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
result = client.predict("fruit-bowl-seg", "production", images=["photo.jpg"])

// typescript
const client = new Aperture({ baseUrl: "http://localhost:8080", apiKey: process.env.APERTURE_KEY });
const result = await client.predict("fruit-bowl-seg", "production", { images: [file] });`}</Code>
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
      title: "Connect an agent",
      body: (
        <>
          <p>
            Aperture's API is also a set of MCP tools: list models, read a model card, look at traffic and failures, open a captured inference,
            run <code>predict</code> and see the overlay. An agent you connect acts as you and nothing more, and what it changes shows up in
            the audit log under your name. Pick by the client you use.
          </p>
          <Rows
            items={[
              [
                "Claude Code, Cursor",
                <>
                  Add <code>/mcp</code> on this instance and sign in when the client asks. Nothing to install, no token to copy.{" "}
                  <code>predict</code> takes image URLs or base64.
                </>,
              ],
              [
                "Claude Desktop",
                <>
                  Install the <code>aperture.mcpb</code> extension from the tokens page. It runs on your computer, so <code>predict</code> can
                  also read local files.
                </>,
              ],
              [
                "From a checkout",
                <>
                  Run the server yourself over stdio, for any MCP client, when you need local files outside Claude Desktop.
                </>,
              ],
            ]}
          />
          <p>
            Signing in uses OAuth: the client opens this console in your browser, you approve it, and it receives a token of its own. That
            grant is listed on the tokens page marked <Ui>OAuth</Ui>; it stays valid while the client keeps using it, lapses after 30 days
            unused, and <Ui>Revoke</Ui> ends it at once.
          </p>
        </>
      ),
    },
    {
      id: "tokens-mcp-claude-code",
      title: "Set up Claude Code",
      body: (
        <>
          <Steps>
            <li>
              Add the server. The tokens page under <Ui>Connect an agent</Ui> shows the command with this instance's address. Add{" "}
              <code>-s user</code> to make Aperture available in every project, not just the current directory.
            </li>
            <li>
              Start <code>claude</code>, run <code>/mcp</code>, select <code>aperture</code> and choose <Ui>Authenticate</Ui>.
            </li>
            <li>
              Your browser opens the <Ui>Connect an application</Ui> page. Sign in if asked, check that it names Claude Code and sends the
              answer to <code>localhost</code>, and click <Ui>Approve</Ui>.
            </li>
            <li>
              Back in the terminal, <code>/mcp</code> shows <code>aperture</code> as connected. Ask something that needs it, for example "which
              models had errors in the last day?".
            </li>
          </Steps>
          <Shot name="oauth-consent" path="/oauth/authorize" alt="The Connect an application page asking to approve Claude Code" />
          <Code title="Claude Code">{`claude mcp add --transport http aperture http://localhost:8080/mcp

# optional: default to one workspace
claude mcp add --transport http aperture http://localhost:8080/mcp \\
  --header "X-Aperture-Workspace: fruit"`}</Code>
          <p>
            Without <code>X-Aperture-Workspace</code>, calls act in the workspace you last opened in the console. Every tool also takes{" "}
            <code>workspace=</code>, and <code>list_workspaces</code> shows the slugs.
          </p>
          <Note title="Without a browser">
            On a machine where no browser can open, such as a CI runner, send a personal access token instead:{" "}
            <code>{`--header "Authorization: Bearer ap_pat_..."`}</code>. The dialog that shows a new token includes this command filled in.
          </Note>
        </>
      ),
    },
    {
      id: "tokens-mcp-cursor",
      title: "Set up Cursor",
      body: (
        <>
          <Steps>
            <li>
              Open <code>.cursor/mcp.json</code> in your project, or <code>~/.cursor/mcp.json</code> for every project, and add the{" "}
              <code>aperture</code> entry below.
            </li>
            <li>
              Open Cursor's MCP settings. <code>aperture</code> shows that it needs a login; click it, approve the request in the browser page
              that opens, and return to Cursor.
            </li>
            <li>
              Check that <code>aperture</code> is enabled and lists its tools.
            </li>
          </Steps>
          <Code title=".cursor/mcp.json">{`{
  "mcpServers": {
    "aperture": {
      "url": "http://localhost:8080/mcp"
    }
  }
}`}</Code>
          <p>
            Add <code>{`"headers": { "X-Aperture-Workspace": "fruit" }`}</code> for a default workspace, or an <code>Authorization</code> header
            with a personal access token to skip signing in. Any other client that speaks streamable HTTP connects the same way.
          </p>
        </>
      ),
    },
    {
      id: "tokens-mcp-desktop",
      title: "Set up Claude Desktop",
      body: (
        <>
          <Steps>
            <li>
              On the <Ui>Personal access tokens</Ui> page, click <Ui>Download aperture.mcpb</Ui>.
            </li>
            <li>
              Open the downloaded file. Claude Desktop shows the Aperture extension; click <Ui>Install</Ui>.
            </li>
            <li>
              Fill in <FieldName>Aperture URL</FieldName> with the address you open the console at, <FieldName>Personal access token</FieldName>{" "}
              with your token, and optionally <FieldName>Default workspace</FieldName> with a workspace slug.
            </li>
            <li>
              Enable the extension and start a new chat. The first start takes a few seconds while Claude Desktop installs its dependencies.
            </li>
          </Steps>
          <p>
            The token is stored by Claude Desktop as a secret. To change it, open Claude Desktop's settings, find Aperture under extensions and
            edit its configuration. When Aperture is upgraded, download the extension again from the tokens page so it matches the instance.
          </p>
          <Note title="As a connector instead">
            When this instance is reachable from the internet over https, you can skip the extension: in Claude Desktop or on claude.ai, add a
            custom connector with the URL <code>https://your-instance/mcp</code> and approve it the same way as above. Claude connects to
            connectors from Anthropic's servers, so an instance only your network can reach needs the extension.
          </Note>
        </>
      ),
    },
    {
      id: "tokens-mcp-local",
      title: "Run it from a checkout",
      body: (
        <>
          <p>
            The same server runs locally over stdio with <code>uv</code> installed and a copy of the repository. Use this when an agent other
            than Claude Desktop needs to send <code>predict</code> files from your disk.
          </p>
          <Code title="Claude Code, local">{`claude mcp add aperture \\
  -e APERTURE_URL=http://localhost:8080 \\
  -e APERTURE_TOKEN=ap_pat_xxxx.yyyy \\
  -e APERTURE_WORKSPACE=fruit \\
  -- uvx --from /path/to/aperture/mcp aperture-mcp`}</Code>
          <p>
            <code>APERTURE_WORKSPACE</code> is optional. A JSON-configured client takes the same command, arguments and environment.
          </p>
        </>
      ),
    },
    {
      id: "tokens-mcp-troubleshoot",
      title: "If it does not connect",
      body: (
        <Rows
          items={[
            [
              <code>unauthenticated</code>,
              "The client has not signed in. Run its authenticate step again (/mcp in Claude Code), or check an Authorization header is spelled Bearer, a space, then the token.",
            ],
            [
              "The browser page says the request cannot be completed",
              "The client's registration or redirect does not match, or the request is stale. Start connecting again from the client; if it persists, remove the server and add it back.",
            ],
            [
              "resource is not this instance",
              <>
                The client used a different address than the instance's <code>AP_PUBLIC_URL</code>, for example <code>127.0.0.1</code> instead
                of <code>localhost</code>. Connect with the address the console uses.
              </>,
            ],
            [
              <>
                <code>token_revoked</code>, <code>token_expired</code>
              </>,
              "Sign in again from the client. With a token in a header, create a new one and put it in the client's config, or reconfigure the extension.",
            ],
            [
              "cannot read files on your machine",
              <>
                The remote server cannot see your disk. Pass an image URL or base64, or use Claude Desktop or a local server for files.
              </>,
            ],
            [
              <>
                <code>model_not_found</code>, <code>no_workspace</code>
              </>,
              <>
                The call ran in another workspace. Pass <code>workspace=</code> or set <code>X-Aperture-Workspace</code>.
              </>,
            ],
            [
              <>
                <code>insufficient_role</code>
              </>,
              "Your role does not allow the action, for the agent as for you. Ask a workspace admin.",
            ],
            [
              "502 or connection refused",
              <>
                The <code>mcp</code> service is not running on the instance. Ask whoever runs Aperture; see Install and upgrade.
              </>,
            ],
          ]}
        />
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
