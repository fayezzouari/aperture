import { Code, FieldName, Note, Role, Rows, Shot, Steps, Ui, type Chapter } from "../kit";

const MODEL = "/models/e2e-part-detection";

export const models: Chapter = {
  id: "models",
  group: "Build",
  title: "Models and versions",
  lede: (
    <>
      <p>
        A model is a named slot - <code>e2e-part-detection</code> - with its own version history. A version is one set of weights plus the
        pre- and post-processing config that goes with them. Products never call a version directly if they can help it: they call a{" "}
        <b>tag</b> such as <code>production</code>, and you move the tag.
      </p>
      <p>The lifecycle is always the same: create the model, upload a version, publish it, deploy it, point a tag at it.</p>
    </>
  ),
  sections: [
    {
      id: "models-list",
      title: "The model list",
      body: (
        <>
          <p>
            <Ui>Models</Ui> lists every model in the workspace with its task, tags, live versions, replicas, requests, error rate and p95 over
            the last 24 hours. Search by slug, name or owner, filter by task, or tick <b>Live only</b>.
          </p>
          <Shot name="models-list" path="/models" alt="The model list of the e2e workspace" />
        </>
      ),
    },
    {
      id: "models-create",
      title: "Create a model",
      body: (
        <>
          <p>
            <Role min="developer" />
          </p>
          <Steps>
            <li>
              Click <Ui>New model</Ui>.
            </li>
            <li>
              Fill in <FieldName>Name</FieldName>. The <FieldName>Slug</FieldName> is derived from it; it is what products put in the URL and it
              cannot change later. It has to be unique in this workspace.
            </li>
            <li>
              Pick the <FieldName>Task</FieldName> - detection, segmentation, classification, keypoints, OCR or embedding. It fixes the response
              shape callers can rely on.
            </li>
            <li>
              Optionally set an <FieldName>Owner</FieldName> (who to ask when it misbehaves) and a <FieldName>Description</FieldName>, then
              click <Ui>Create model</Ui>.
            </li>
          </Steps>
          <Shot name="model-new" path="/models" alt="The New model dialog with name, slug, task, owner and description" />
        </>
      ),
    },
    {
      id: "models-import",
      title: "Import from Roboflow",
      body: (
        <>
          <p>
            <Role min="developer" /> Instead of uploading weights yourself, you can pull a trained Roboflow model. The import downloads the
            weights and the version's class list, converts them to ONNX and lands a draft version.
          </p>
          <Steps>
            <li>
              Click <Ui>Import from Roboflow</Ui>.
            </li>
            <li>
              Paste the page you were on into <FieldName>Model reference</FieldName> - the version page, the trained-model page, or{" "}
              <code>workspace/project/version</code>.
            </li>
            <li>
              Optionally set <FieldName>Model slug</FieldName>; it defaults to the Roboflow project id, and an existing model of that slug gains
              a new version.
            </li>
            <li>
              Leave <FieldName>API key</FieldName> empty to use the instance key (<code>AP_ROBOFLOW_API_KEY</code>), or paste one for a
              different account. Click <Ui>Start import</Ui> and follow it on the Jobs page.
            </li>
          </Steps>
          <Shot name="model-import" path="/models" alt="The Import from Roboflow dialog" />
          <Note>
            Imports run on the converter, which is an optional profile: <code>docker compose --profile convert up -d converter</code>.
          </Note>
        </>
      ),
    },
    {
      id: "models-versions",
      title: "Versions",
      body: (
        <>
          <p>
            Open a model. The <Ui>Versions</Ui> tab lists every version with its status, tags, replicas, format and size, checksum, dataset,
            training run, commit and metrics. Published weights are immutable: a change is a new version.
          </p>
          <Shot name="model-versions" path={MODEL} alt="The versions of e2e-part-detection: two ready versions, production on v1" />
          <Rows
            items={[
              ["draft", "Uploaded, config still editable. Try it in the playground; publish it to deploy or tag it."],
              ["ready", "Published. Config frozen; can be tagged and deployed."],
              ["failed", "The runtime could not load it; fix the config or upload again."],
              ["deprecated", "Kept for history and rollbacks."],
            ]}
          />
        </>
      ),
    },
    {
      id: "models-upload",
      title: "Upload a version",
      body: (
        <>
          <p>
            <Role min="developer" />
          </p>
          <Steps>
            <li>
              Click <Ui>New version</Ui> (or <Ui>Upload version 1</Ui> on a new model).
            </li>
            <li>
              Drop the weights file: <code>.onnx</code>, <code>.pt</code>, <code>.torchscript</code>, <code>.safetensors</code> or a TensorRT{" "}
              <code>.engine</code>. Large files go up in resumable chunks and are checksummed end to end.
            </li>
            <li>
              Choose the config. For a retrained model keep <FieldName>Inherit vN's config</FieldName> ticked - same architecture, new
              weights. Otherwise pick the preset that matches the architecture (YOLO11, DETR, a classifier…).
            </li>
            <li>
              Optionally record the <FieldName>Training run ID</FieldName>, <FieldName>Dataset version</FieldName>,{" "}
              <FieldName>Git commit</FieldName>, <FieldName>Metrics (JSON)</FieldName> such as <code>{`{"mAP50": 0.71}`}</code> and a one-line{" "}
              <FieldName>Changelog</FieldName>. The Compare tab uses them.
            </li>
            <li>
              Click <Ui>Upload</Ui>.
            </li>
          </Steps>
          <Shot name="version-upload" path={MODEL} alt="The New version dialog with a weights file chosen and the config inherited" />
          <p>
            When the file needs conversion first - a PyTorch checkpoint, say - the version card says so and offers <Ui>Convert to ONNX</Ui>.
            Conversion lands as a new draft version and leaves the original alone.
          </p>
        </>
      ),
    },
    {
      id: "models-publish-tag",
      title: "Publish and tag",
      body: (
        <>
          <Steps>
            <li>
              <Role min="developer" /> Click <Ui>Publish</Ui> on a draft. It freezes the config. No reference dataset is needed: a runtime
              loading the weights is the check, and the card records it.
            </li>
            <li>
              Click <Ui>Tag</Ui> on a ready version, type the <FieldName>Tag</FieldName> (<code>production</code>, <code>staging</code>, or
              any name your products use) and click <Ui>Move tag</Ui>.
            </li>
          </Steps>
          <Shot name="version-tag" path={MODEL} alt="The Point a tag at v1 dialog" />
          <Note kind="warn" title="Deploy before you tag">
            Moving a tag switches live traffic on the next request. If the version has no replicas yet, callers using the tag get{" "}
            <code>model_not_deployed</code> until it is deployed - so deploy first, then move the tag.
          </Note>
        </>
      ),
    },
    {
      id: "models-playground",
      title: "Try it in the playground",
      body: (
        <>
          <p>
            The <Ui>Playground</Ui> tab runs a version on your own images through a real replica - the same preprocessing, forward pass and
            decoder that serve production. If the version has no replica, it starts one and the first run waits for the weights to load.
          </p>
          <Steps>
            <li>
              Pick a <FieldName>Version or tag</FieldName>. A tag resolves at request time, so it shows what production really serves.
            </li>
            <li>Drop one or more images; several go in one batched request.</li>
            <li>
              Optionally override <FieldName>Score</FieldName>, <FieldName>IoU</FieldName> or <FieldName>Top-k</FieldName> for this request
              only.
            </li>
            <li>
              Click <Ui>Run inference</Ui>. The result shows the overlay, the JSON, and the latency per stage with the runtime that served it.
            </li>
          </Steps>
          <Shot name="playground" path={`${MODEL}?tab=playground`} alt="The playground: an image with detection boxes and the latency breakdown" />
          <p>
            <b>Call it from code</b> under the input shows the same request as <code>curl</code>, with your overrides filled in.
          </p>
        </>
      ),
    },
    {
      id: "models-config",
      title: "Pre- and post-processing config",
      body: (
        <>
          <p>
            The <Ui>Config</Ui> tab shows a version's config: input size, layout and colour order, resize mode, normalisation, the decoder,
            thresholds, NMS, the class list and warm-up. It is editable while the version is a draft and frozen once published.
          </p>
          <Shot name="model-config" path={`${MODEL}?tab=config`} alt="The config tab: input, resize, normalise, decode, classes and warm-up" />
          <Note title="The class list">
            Classes go one per line, in the order the model emits them. A mismatch is the most common cause of confidently wrong labels.
          </Note>
          <p>
            For heads the built-in decoders cannot express, <b>Custom hook</b> runs a Python <code>postprocess</code> function in a separate
            process with no filesystem or network access and a hard CPU and memory cap.
          </p>
        </>
      ),
    },
    {
      id: "models-deploy",
      title: "Deploy, scale and roll back",
      body: (
        <>
          <p>
            The <Ui>Deploy</Ui> tab lists what runs: each deployment with its mode, replicas and where they live, and every tag with what it
            points at.
          </p>
          <Shot name="model-deploy" path={`${MODEL}?tab=deploy`} alt="The deploy tab: two deployments with their replicas, and the production tag" />
          <Steps>
            <li>
              <Role min="developer" /> Click <Ui>Deploy a version</Ui>.
            </li>
            <li>
              Choose the <FieldName>Version</FieldName> and the <FieldName>Mode</FieldName>: <b>primary</b> answers tag and version calls,{" "}
              <b>canary</b> takes a slice of tag traffic (set by <FieldName>Traffic share</FieldName>), <b>shadow</b> mirrors traffic and answers
              nothing.
            </li>
            <li>
              Set <FieldName>Replicas</FieldName> and the <FieldName>Device</FieldName> (<code>auto</code> prefers a GPU runtime).
            </li>
            <li>
              Tune batching if needed: <FieldName>Max batch</FieldName> requests merged per forward pass, <FieldName>Batch wait (ms)</FieldName>{" "}
              to wait for company, <FieldName>Queue depth</FieldName> beyond which callers get <code>503 queue_full</code>, and the{" "}
              <FieldName>Request timeout (ms)</FieldName>.
            </li>
            <li>
              Optionally tick <FieldName>Autoscale on queue depth</FieldName> with min and max replicas and a target depth. Click <Ui>Deploy</Ui>.
            </li>
          </Steps>
          <Shot name="deploy-dialog" path={`${MODEL}?tab=deploy`} alt="The Deploy a version dialog with mode, replicas, device and batching" />
          <p>
            A new version warms up on its own replicas first; traffic only moves when you move the tag. On a deployment, <Ui>+</Ui> and{" "}
            <Ui>−</Ui> scale it and <Ui>Stop</Ui> drains it - in-flight requests finish first.
          </p>
          <p>
            <Ui>Roll back</Ui> next to a tag repoints it at the version it pointed to before its last move. It is instant and needs no
            redeploy, but the previous version must still be deployed - keep it on one replica while a new one beds in.
          </p>
        </>
      ),
    },
    {
      id: "models-traffic",
      title: "Traffic and drift",
      body: (
        <>
          <p>
            The <Ui>Traffic</Ui> tab charts requests, images, error rate and latency percentiles for one model over 1 hour to 30 days,
            optionally for one version. Below are the cost of each stage, the errors by code, and drift signals - confidence, class mix and input
            statistics against a baseline window, rolled up hourly.
          </p>
          <Shot name="model-traffic" path={`${MODEL}?tab=traffic`} alt="The traffic tab: request, error and latency tiles, latency chart and errors by code" />
          <p>
            <Ui>Open in the explorer</Ui> jumps to the inference log filtered to this model.
          </p>
        </>
      ),
    },
    {
      id: "models-compare",
      title: "Compare two versions",
      body: (
        <>
          <p>
            The <Ui>Compare</Ui> tab puts two versions side by side: upload metadata, a week of traffic, error rate and latency, the metrics
            recorded at upload with their deltas, and every config field that differs.
          </p>
          <Shot name="model-compare" path={`${MODEL}?tab=compare`} alt="Comparing v1 and v2: metadata, metrics and config differences" />
        </>
      ),
    },
    {
      id: "models-call",
      title: "Call a model from a product",
      body: (
        <>
          <p>
            Products authenticate with an API key of the model's workspace (see <a href="#keys">API keys</a>). The path takes the model slug
            and a tag or a version number.
          </p>
          <Code title="Predict">{`curl -X POST "http://localhost:8080/v1/models/e2e-part-detection/production/predict" \\
  -H "x-api-key: $APERTURE_KEY" \\
  -F "images=@photo.jpg"

# a version instead of a tag:      .../e2e-part-detection/2/predict
# several images in one request:   -F images=@a.jpg -F images=@b.jpg
# per-request thresholds:          -F 'overrides={"score_threshold": 0.4}'
# a rendered overlay back:         -F include_overlay=true
# Roboflow-shaped results:         ...?format=roboflow`}</Code>
          <p>
            Every response names the version that served it, carries a request id (also in the <code>x-request-id</code> header) and a
            latency breakdown. Errors share one contract - <code>{`{"error": {"code", "message", "request_id"}}`}</code> - with stable codes
            such as <code>model_not_deployed</code>, <code>invalid_image</code>, <code>rate_limited</code> and <code>queue_full</code>.
          </p>
        </>
      ),
    },
  ],
};
