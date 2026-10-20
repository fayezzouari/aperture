"""The overview's log-sink backlog counts what is still to write, not the stream's length."""

from __future__ import annotations

import redis.asyncio as aioredis

from app import cache


class StubRedis:
    def __init__(self, groups=None, length=0, missing=False):
        self.groups = groups or []
        self.length = length
        self.missing = missing

    async def xinfo_groups(self, stream):
        if self.missing:
            raise aioredis.ResponseError("no such key")
        return self.groups

    async def xlen(self, stream):
        return self.length


async def test_written_records_do_not_count_as_backlog(monkeypatch):
    # 10,647 records in the stream, every one of them written and acknowledged
    stub = StubRedis([{"name": b"logsink", "pending": 0, "lag": 0}], length=10_647)
    monkeypatch.setattr(cache, "client", lambda: stub)
    assert await cache.stream_depth() == 0


async def test_backlog_is_pending_plus_not_yet_delivered(monkeypatch):
    stub = StubRedis([{"name": "logsink", "pending": 40, "lag": 2_000}], length=50_000)
    monkeypatch.setattr(cache, "client", lambda: stub)
    assert await cache.stream_depth() == 2_040


async def test_without_a_sink_everything_in_the_stream_is_backlog(monkeypatch):
    monkeypatch.setattr(cache, "client", lambda: StubRedis([], length=7_000))
    assert await cache.stream_depth() == 7_000


async def test_no_stream_yet_means_no_backlog(monkeypatch):
    monkeypatch.setattr(cache, "client", lambda: StubRedis(missing=True))
    assert await cache.stream_depth() == 0
