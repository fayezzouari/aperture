import { Code, FieldName, Note, Role, Rows, Shot, Steps, Ui, type Chapter } from "../kit";

export const workflows: Chapter = {
  id: "workflows",
  group: "Build",
  title: "Workflows",
  lede: (
    <p>
      A workflow chains models and transforms into one endpoint - detect the fruit, crop to each one, grade its ripeness - so a product calls one URL
      instead of re-implementing the pipeline. It is a graph you draw on a canvas; what you draw is literally what runs. Workflows belong to
      the active workspace, and their model nodes can only use that workspace's models.
    </p>
  ),
  sections: [
    {
      id: "workflows-list",
      title: "The workflow list",
      body: (
        <>
          <p>
            Open <Ui>Workflows</Ui> under <b>Build</b> in the sidebar. Each row shows the state (<code>r3</code> once published, otherwise{" "}
            <code>draft</code>, plus an issue count when the graph has problems), the models it calls in order, runs and errors in the last 24
            hours, and p95 latency. The panel underneath shows the call a product makes.
          </p>
          <Shot name="workflows-list" path="/workflows" alt="The workflow list with the e2e pipeline, published at revision 1" />
        </>
      ),
    },
    {
      id: "workflows-create",
      title: "Create a workflow",
      body: (
        <>
          <p>
            <Role min="developer" /> Creating starts you from a runnable starter graph: <b>Images in → Detect → Result</b>.
          </p>
          <Steps>
            <li>
              Click <Ui>New workflow</Ui> at the top right.
            </li>
            <li>
              Type a <FieldName>Name</FieldName>. The <FieldName>Slug</FieldName> fills in from it - products call{" "}
              <code>/v1/workflows/&#123;slug&#125;/run</code>, so pick it with care. Lowercase letters, digits, dot, dash or underscore. It
              has to be unique in the workspace, not across the instance.
            </li>
            <li>
              Optionally fill <FieldName>Owner</FieldName> and <FieldName>Description</FieldName>.
            </li>
            <li>
              Click <Ui>Create and open the canvas</Ui>. You land in the editor.
              <Shot name="workflows-new" path="/workflows" alt="The New workflow dialog with name, slug, owner and description" />
            </li>
          </Steps>
          <Note>
            A fresh starter graph has exactly one problem - <i>the model node 'Detect' has no model set</i> - until you choose a model and save.
          </Note>
        </>
      ),
    },
    {
      id: "workflows-editor",
      title: "Build the graph",
      body: (
        <>
          <p>
            The editor has two modes, <Ui>Build</Ui> and <Ui>Try it</Ui>. Build shows the canvas, the <b>Add</b> palette above it and the node
            inspector on the right.
          </p>
          <Shot
            name="workflow-editor"
            path="/workflows/e2e-orchard-pipeline"
            alt="The workflow editor: a two-stage graph that counts fruit, crops to each one and grades it"
            caption="The e2e pipeline: detect the fruit, crop to every detection, attach each crop's box, then grade every crop for ripeness."
          />
          <Steps>
            <li>
              Add a node with a palette button: <Ui>Model</Ui>, <Ui>Gate</Ui>, <Ui>Crop to detections</Ui>, <Ui>Filter</Ui>,{" "}
              <Ui>Box geometry</Ui>, <Ui>Merge</Ui> or <Ui>Result</Ui>. The new node is selected so its inspector opens straight away.
            </li>
            <li>
              Connect nodes by dragging from a node's <b>right</b> edge to another node's <b>left</b> edge. Select an edge or a node and press{" "}
              <code>Backspace</code> to remove it, or use <Ui>Delete node</Ui> in the inspector.
            </li>
            <li>
              Click a node to configure it in the <b>Node</b> panel. Every node has a <FieldName>Label</FieldName>, shown on the canvas and in
              the per-node timings.
              <Shot name="workflow-inspector" path="/workflows/e2e-orchard-pipeline" alt="The inspector for a model node: model, version or tag, score threshold" />
            </li>
            <li>
              Click <Ui>Save</Ui>. An <code>unsaved</code> badge next to the title tells you when the canvas has changes the server has not seen.
              Saving a published workflow puts it back to <code>draft</code>, but the published revision keeps serving until you publish again.
            </li>
          </Steps>
          <p>
            The canvas has zoom and fit-view controls at the bottom left and a minimap at the bottom right. Moving nodes, connecting and the
            palette are available from the developer role up.
          </p>
        </>
      ),
    },
    {
      id: "workflows-nodes",
      title: "Node reference",
      body: (
        <>
          <p>Every node passes a list of items (images or crops, with their predictions) to the nodes it connects to.</p>
          <Rows
            items={[
              ["Images in", "Where the request's images enter. Exactly one per graph; it cannot be deleted."],
              [
                "Model",
                <>
                  Runs a deployed model on everything that reaches it, in one runtime call. <FieldName>Model</FieldName> lists the workspace's
                  models; <FieldName>Version or tag</FieldName> takes a tag (resolved at run time, so the workflow follows your rollbacks) or a
                  ready version. <FieldName>Score threshold</FieldName> overrides the model's threshold for this node only. At most 64 items per
                  node.
                </>,
              ],
              [
                "Gate",
                <>
                  Runs the branch only when a run parameter passes a test: <FieldName>Run parameter</FieldName> names a key in the run's{" "}
                  <code>params</code>, <FieldName>Test</FieldName> is <i>list contains</i>, <i>equals</i> or <i>is set</i>. A closed gate is
                  not an error - its result key is simply missing from the response.
                </>,
              ],
              [
                "Crop to detections",
                <>
                  One crop per upstream detection, highest score first. <FieldName>Max crops</FieldName> per image (1-64, default 8),{" "}
                  <FieldName>Padding</FieldName> as a fraction of the box (0.05), <FieldName>Minimum score</FieldName> (0.3),{" "}
                  <FieldName>Only these classes</FieldName> (comma separated, empty means all) and <FieldName>Square crops</FieldName>.
                </>,
              ],
              [
                "Filter",
                <>
                  Keeps the predictions worth passing on: <FieldName>Minimum score</FieldName> (0.5), <FieldName>Keep at most</FieldName> per
                  image (5), <FieldName>Only these classes</FieldName>.
                </>,
              ],
              [
                "Box geometry",
                <>
                  Gives each crop its own box back as a named model input, so a position-aware second stage sees the geometry it was trained
                  with. <FieldName>Input name</FieldName> must match the model graph; <FieldName>Format</FieldName> is <code>cx, cy, w, h</code>{" "}
                  or <code>x1, y1, x2, y2</code>, normalised.
                </>,
              ],
              ["Merge", "Brings branches back together: the items and results of every parent."],
              [
                "Result",
                <>
                  What the API returns, under <FieldName>Result key</FieldName>. <FieldName>Response shape</FieldName> is Aperture (typed,
                  corner boxes) or Roboflow compatible (centre-point boxes, <code>confidence</code> and <code>class</code>).
                </>,
              ],
            ]}
          />
        </>
      ),
    },
    {
      id: "workflows-problems",
      title: "Problems and publishing",
      body: (
        <>
          <p>
            Under the header, a banner lists what stops the saved graph from being published: an empty graph, no or several <b>Images in</b>{" "}
            nodes, no <b>Result</b>, a model node without a model, a gate without a parameter, a node with nothing connected to its input, a
            cycle, or more than 40 nodes.
          </p>
          <Steps>
            <li>Fix every problem the banner lists, then save.</li>
            <li>
              <Role min="developer" /> Click <Ui>Publish</Ui>. It saves first, then publishes: the revision goes up by one and callers get the
              new graph immediately. If the graph still has problems, the publish is refused with the list.
            </li>
          </Steps>
        </>
      ),
    },
    {
      id: "workflows-try",
      title: "Try it on an image",
      body: (
        <>
          <p>
            <Ui>Try it</Ui> runs the graph as it stands on the canvas, published or not. Unsaved changes are saved first.
          </p>
          <Steps>
            <li>
              Switch to <Ui>Try it</Ui>.
            </li>
            <li>
              Drop one or more images on <b>Drop an image</b>, or click it to choose files.
            </li>
            <li>
              Click <Ui>Run workflow</Ui> <Role min="developer" />.
            </li>
            <li>
              Read the result. <b>Where the time went</b> lists every node with its share of the total; <b>What each stage saw</b> shows the
              intermediate images (the input and every crop); <b>Output</b> is the exact JSON a caller gets.
              <Shot name="workflow-try" path="/workflows/e2e-orchard-pipeline" alt="A workflow run: per-node timings, the crops each stage saw and the output JSON" />
            </li>
            <li>
              Switch back to <Ui>Build</Ui>: each node now shows its time, items out and the version that served it, and a node that failed is
              outlined in red with its error.
            </li>
          </Steps>
          <Note>
            The editor cannot send run <code>params</code>, so a Gate testing <i>list contains</i> or <i>equals</i> stays closed in Try it.
            Exercise gates through the API.
          </Note>
        </>
      ),
    },
    {
      id: "workflows-call",
      title: "Call a published workflow",
      body: (
        <>
          <p>
            Products call the published revision with an API key of the same workspace. A workflow that was never published answers{" "}
            <code>model_not_deployed</code>; only a signed-in user can run a draft.
          </p>
          <Code title="Run a workflow">{`curl -X POST "http://localhost:8080/v1/workflows/e2e-orchard-pipeline/run" \\
  -H "x-api-key: $APERTURE_KEY" \\
  -F "images=@photo.jpg" \\
  -F 'params={"regions": ["north"]}'

# → { "outputs": { "fruit": [...], "ripeness": [...] },
#     "node_stats": [...], "model_calls": 2, "total_ms": 154 }`}</Code>
          <p>
            Add <code>?format=roboflow</code> for Roboflow-shaped results; a Result node's own shape setting wins over it. A node that fails
            mid-run still answers HTTP 200, with <code>status: "error"</code> and the failed node named.
          </p>
        </>
      ),
    },
  ],
};
