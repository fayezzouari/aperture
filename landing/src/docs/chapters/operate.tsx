import { Code, FieldName, Note, Role, Rows, Shot, Steps, Ui, type Chapter } from "../kit";

export const operate: Chapter = {
  id: "operate",
  group: "Operate",
  title: "Watch what is running",
  lede: (
    <p>
      Every request that reaches the gateway is logged with its version, caller, outcome, predictions and a per-stage latency breakdown. A
      sample of them - and every failure - is captured with its image, so you can see exactly what the model saw.
    </p>
  ),
  sections: [
    {
      id: "operate-inferences",
      title: "Inferences",
      body: (
        <>
          <p>
            <Ui>Inferences</Ui> is the explorer for the active workspace. Filter by time window, model, outcome, flag state, capture, minimum
            latency or confidence, request id or trace id; filters apply as you set them, and the list refreshes every 20 seconds.
          </p>
          <Shot name="traces" path="/traces" alt="The inference explorer with filters and a table of requests" />
          <Steps>
            <li>Click a row to open the request.</li>
            <li>
              Read the latency bar, the error if there was one, and - for a captured request - <b>What the model saw</b>: the image with the
              predictions drawn over it.
            </li>
            <li>
              Below are the caller, route, tag, batch, runtime and trace id, the prediction summary and the raw output.
            </li>
          </Steps>
          <Shot name="trace-detail" path="/traces" alt="One inference: latency, the captured image with its boxes, request and prediction details" />
        </>
      ),
    },
    {
      id: "operate-flag",
      title: "Flag predictions for retraining",
      body: (
        <>
          <Steps>
            <li>Open a captured request.</li>
            <li>
              Choose what was wrong in <FieldName>Flag as</FieldName> - false positive, missed detection, wrong class, bad box or other - and
              add a <FieldName>Note</FieldName> if useful.
            </li>
            <li>
              Click <Ui>Flag for retraining</Ui>. <Ui>Unflag</Ui> takes it back.
            </li>
            <li>
              When you have enough, go to <Ui>Settings</Ui> › <Ui>Capture &amp; retention</Ui> and click <Ui>Export flagged predictions</Ui>{" "}
              <Role min="developer" />. You get this workspace's flagged images with the model's boxes as a COCO dataset, from the Jobs page.
            </li>
          </Steps>
          <Note>
            The boxes in the export are the model's predictions, not verified labels. Correct them in your annotation tool before training. A
            request that was not captured has no image and cannot be flagged.
          </Note>
        </>
      ),
    },
    {
      id: "operate-fleet",
      title: "Fleet",
      body: (
        <>
          <p>
            <Ui>Fleet</Ui> shows every runtime with its device, memory and slots, and every version it holds with queue depth, requests and
            memory. Runtimes pull their assignments on each heartbeat, so scaling a deployment needs no restart; a runtime that stops reporting
            turns <code>stale</code> and its replicas are placed elsewhere. A version that does not fit in a runtime's memory is refused there
            with the reason.
          </p>
          <Shot name="fleet" path="/fleet" alt="The fleet: runtimes with the versions they hold" />
          <p>
            <Ui>Reconcile now</Ui> runs the placement immediately instead of waiting for the next tick.
          </p>
        </>
      ),
    },
    {
      id: "operate-jobs",
      title: "Jobs",
      body: (
        <>
          <p>
            Long work never runs on the inference path: imports, conversions, bulk folders, video, exports and capture purges are jobs. The
            page lists the workspace's jobs with status, progress and result. <Ui>Download</Ui> fetches an artifact; <Ui>Cancel</Ui> stops a
            queued or running job after its current batch.
          </p>
          <Shot name="jobs" path="/jobs" alt="The jobs page with a finished export" />
          <Code title="Submit a job from code">{`curl -X POST "http://localhost:8080/v1/jobs" \\
  -H "authorization: Bearer $APERTURE_TOKEN" \\
  -H "x-aperture-workspace: e2e" \\
  -H "content-type: application/json" \\
  -d '{"kind": "bulk_images", "model": "e2e-orchard", "ref": "production",
       "source_urls": ["https://example.com/photos/0001.jpg"],
       "webhook_url": "https://example.com/aperture-callback"}'
# -> 202 {"id": "...", "status": "queued"}
# GET /v1/jobs/{id} to poll, or wait for the webhook.
# kind "video" takes video_key and frame_stride instead.`}</Code>
        </>
      ),
    },
    {
      id: "operate-reports",
      title: "Reports",
      body: (
        <>
          <p>
            <Ui>Reports</Ui> answers <i>who called what</i>: usage by model, version, consumer, day or error code; error breakdowns; latency
            trends; flagged predictions - over 24 hours to 90 days. <Ui>Export CSV</Ui> and <Ui>Export PDF</Ui> download what you see.
          </p>
          <Shot name="reports" path="/reports" alt="A usage report grouped by model" />
          <Steps>
            <li>
              Set up the report you want, then click <Ui>Schedule</Ui>.
            </li>
            <li>
              Give it a <FieldName>Name</FieldName>, pick a <FieldName>Format</FieldName>, a <FieldName>Cron (UTC)</FieldName> such as{" "}
              <code>0 7 * * 1</code> (Mondays at 07:00), and where to <FieldName>Deliver via</FieldName> - Slack, a webhook or email - with
              its <FieldName>Destination</FieldName>.
            </li>
            <li>
              Click <Ui>Create schedule</Ui>. <Ui>Run now</Ui> on a schedule sends one immediately.
            </li>
          </Steps>
          <Shot name="report-schedule" path="/reports" alt="The Schedule this report dialog" />
        </>
      ),
    },
    {
      id: "operate-alerts",
      title: "Alerts",
      body: (
        <>
          <p>
            Alert rules are evaluated every minute. A rule needs a minimum number of samples before it can fire, so a quiet model stays quiet.
            <b> Recent alert events</b> lists every firing and resolution with whether it was delivered, so a silent webhook is visible.
          </p>
          <Shot name="alerts" path="/alerts" alt="The alerts page with one rule and recent events" />
          <Steps>
            <li>
              <Role min="developer" scope="instance" /> Click <Ui>New rule</Ui>.
            </li>
            <li>
              Choose the <FieldName>Metric</FieldName> and a <FieldName>Comparator</FieldName> with a <FieldName>Threshold</FieldName>:
            </li>
          </Steps>
          <Rows
            items={[
              ["Error rate", "Fraction of non-ok responses. 0.02 means 2%."],
              ["p95 / p99 latency", "End-to-end latency percentile, in milliseconds."],
              ["Replicas missing", "Replicas requested but not ready. Fires on 1 or more."],
              ["No traffic", "Requests in the window. Use with below to catch a consumer that went silent."],
              ["Mean confidence", "Average prediction confidence. Use with below."],
            ]}
          />
          <Steps>
            <li>
              Set the <FieldName>Window (min)</FieldName>, the <FieldName>Scope</FieldName> (every model or one) and the{" "}
              <FieldName>Minimum samples</FieldName>.
            </li>
            <li>
              Pick a <FieldName>Channel</FieldName> - Slack, webhook or email (needs <code>AP_SMTP_HOST</code>) - and its{" "}
              <FieldName>Destination</FieldName>. Click <Ui>Create rule</Ui>, then <Ui>Test</Ui> to send a test message.
            </li>
          </Steps>
          <Shot name="alert-new" path="/alerts" alt="The New alert rule dialog" />
          <Note>Alerts and reports are instance-wide: a rule scoped to every model watches every workspace.</Note>
        </>
      ),
    },
  ],
};
