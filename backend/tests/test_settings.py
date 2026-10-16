"""Instance settings: capture overrides that fall back to the AP_CAPTURE_* defaults."""

from __future__ import annotations

from app.settings import settings


async def test_defaults_show_through_until_overridden(client, viewer):
    body = (await client.get("/v1/settings", headers=viewer.headers)).json()
    assert body["capture_sample_rate"] is None
    assert body["effective_sample_rate"] == settings.capture_sample_rate
    assert body["effective_retention_days"] == settings.capture_retention_days


async def test_admin_overrides_and_clears(client, admin):
    set_ = await client.patch("/v1/settings",
                              json={"capture_sample_rate": 0.5, "capture_retention_days": 7},
                              headers=admin.headers)
    assert set_.status_code == 200, set_.text
    assert set_.json() == {"capture_sample_rate": 0.5, "capture_retention_days": 7,
                           "effective_sample_rate": 0.5, "effective_retention_days": 7}

    cleared = await client.patch("/v1/settings", json={"capture_sample_rate": None},
                                 headers=admin.headers)
    body = cleared.json()
    assert body["capture_sample_rate"] is None
    assert body["effective_sample_rate"] == settings.capture_sample_rate
    assert body["capture_retention_days"] == 7  # left alone because it was not sent


async def test_only_admins_change_settings(client, developer):
    response = await client.patch("/v1/settings", json={"capture_sample_rate": 1.0},
                                  headers=developer.headers)
    assert response.status_code == 403


async def test_rate_is_bounded(client, admin):
    response = await client.patch("/v1/settings", json={"capture_sample_rate": 2},
                                  headers=admin.headers)
    assert response.status_code == 422
