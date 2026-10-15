"""Job rows reach the agent without credentials, whatever the API sent."""

from __future__ import annotations

from aperture_mcp.trim import job_row


def test_job_row_masks_credentials():
    row = job_row({"id": "j1", "kind": "roboflow_import", "status": "queued",
                   "params": {"api_key": "rf_live_secret", "ref": "ws/p/3",
                              "steps": [{"password": "pw"}]}})
    assert row["params"]["api_key"] == "***"
    assert row["params"]["steps"][0]["password"] == "***"
    assert row["params"]["ref"] == "ws/p/3"
