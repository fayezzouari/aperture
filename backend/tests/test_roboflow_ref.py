"""Every Roboflow page people paste resolves to the model they were looking at."""

from __future__ import annotations

import httpx
import pytest

from app.errors import ApiError
from app.jobs.tasks import _roboflow_model_version, parse_roboflow_ref


@pytest.mark.parametrize("ref", [
    "acme/widgets/7",
    "https://app.roboflow.com/acme/widgets/7",
    "https://app.roboflow.com/acme/widgets/7/images",
    "https://app.roboflow.com/acme/widgets/models/widgets/7",
])
def test_a_version_page_names_the_version(ref):
    assert parse_roboflow_ref(ref) == ("acme", "widgets", 7, None)


def test_a_trained_model_page_names_the_model_and_leaves_the_version_to_roboflow():
    ref = "https://app.roboflow.com/acme/widgets/models/acme/sprocket-detector-t2"
    assert parse_roboflow_ref(ref) == ("acme", "widgets", None, "sprocket-detector-t2")


def test_anything_else_is_refused():
    with pytest.raises(ApiError):
        parse_roboflow_ref("acme/widgets")


def _listing(models):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/acme/widgets/models"
        return httpx.Response(200, json=models)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_the_version_comes_from_roboflow_not_the_name():
    # the name says 3; Roboflow says 9, and Roboflow is right
    models = [{"url": "acme/widgets-3-other", "version": "4"},
              {"url": "acme/widgets-3-rfdetr", "version": "9"}]
    async with _listing(models) as client:
        assert await _roboflow_model_version(client, "acme", "widgets",
                                             "widgets-3-rfdetr", "key") == 9


async def test_an_unknown_model_says_which_exist():
    async with _listing([{"url": "acme/one", "version": "1"}]) as client:
        with pytest.raises(ApiError) as caught:
            await _roboflow_model_version(client, "acme", "widgets", "two", "key")
    assert caught.value.details == {"known": ["one"]}
