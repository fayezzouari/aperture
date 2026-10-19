"""R4.5 - replica routing: ready-only, least-loaded, eviction, and a single safe retry."""

from __future__ import annotations

import time

import pytest

from app import cache
from app.errors import ApiError
from app.services import gateway


class FakeRedis:
    """The handful of Redis commands the replica registry uses, in memory."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.counters: dict[str, int] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def mget(self, keys):
        return [self.kv.get(k) for k in keys]

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def zrangebyscore(self, key, low, high):
        return [m for m, score in self.zsets.get(key, {}).items() if score >= low]

    async def zrem(self, key, member):
        self.zsets.get(key, {}).pop(member, None)

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self.r, self.ops = redis, []

    def set(self, key, value, ex=None):
        self.ops.append(lambda: self.r.kv.__setitem__(key, value))

    def delete(self, key):
        self.ops.append(lambda: self.r.kv.pop(key, None))

    def sadd(self, key, member):
        self.ops.append(lambda: self.r.sets.setdefault(key, set()).add(member))

    def srem(self, key, member):
        self.ops.append(lambda: self.r.sets.get(key, set()).discard(member))

    def zadd(self, key, mapping):
        self.ops.append(lambda: self.r.zsets.setdefault(key, {}).update(mapping))

    def zrem(self, key, member):
        self.ops.append(lambda: self.r.zsets.get(key, {}).pop(member, None))

    def expire(self, key, ttl):
        self.ops.append(lambda: None)

    async def execute(self):
        for op in self.ops:
            op()


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "client", lambda: fake)
    gateway._sent.clear()
    return fake


async def beat(runtime_id, url, ready, *, queues=None, in_flight=None):
    await cache.register_runtime(runtime_id, {
        "url": url, "status": "ready", "loaded": ready,
        "queues": queues or {}, "in_flight": in_flight or {}}, ready)


def reply(runtime_id):
    return gateway.RuntimeReply(runtime_id=runtime_id, results=[], latency={}, overlays=[],
                                stats=[], batch_executed=1)


async def test_only_replicas_reporting_the_version_ready_are_routed(redis):
    await beat("rt-a", "http://a:9100", ["v1"])
    await beat("rt-b", "http://b:9100", ["v1"])
    await beat("rt-b", "http://b:9100", [])  # b dropped v1 (unloaded, or failed a reload)
    assert [r["runtime_id"] for r in await cache.healthy_runtimes_for_version("v1")] == ["rt-a"]


async def test_a_restarted_runtime_replaces_its_old_id(redis):
    await beat("rt-a-old", "http://a:9100", ["v1"])
    await beat("rt-a-new", "http://a:9100", [])  # same container, new process, nothing loaded
    assert await cache.healthy_runtimes_for_version("v1") == []
    assert "rt-a-old" not in redis.sets["aperture:runtimes"]


async def test_least_loaded_replica_wins(redis):
    await beat("rt-a", "http://a:9100", ["v1"], queues={"v1": 5}, in_flight={"v1": 2})
    await beat("rt-b", "http://b:9100", ["v1"], queues={"v1": 0}, in_flight={"v1": 1})
    picked = {(await gateway.pick_replica("v1", "m"))["runtime_id"] for _ in range(5)}
    assert picked == {"rt-b"}


async def test_requests_sent_since_the_heartbeat_count_as_load(redis, monkeypatch):
    await beat("rt-a", "http://a:9100", ["v1"])
    await beat("rt-b", "http://b:9100", ["v1"])
    gateway._sent[("rt-a", "v1")] = 3  # three calls to a still in flight from this process
    assert (await gateway.pick_replica("v1", "m"))["runtime_id"] == "rt-b"


async def test_equal_load_spreads_round_robin(redis):
    await beat("rt-a", "http://a:9100", ["v1"])
    await beat("rt-b", "http://b:9100", ["v1"])
    picked = [(await gateway.pick_replica("v1", "m"))["runtime_id"] for _ in range(4)]
    assert set(picked) == {"rt-a", "rt-b"}


async def test_a_replica_that_lost_the_version_is_evicted_and_the_call_retried(
        redis, monkeypatch):
    await beat("rt-a", "http://a:9100", ["v1"])
    await beat("rt-b", "http://b:9100", ["v1"], queues={"v1": 1})  # a is picked first
    calls = []

    async def fake_call(replica, payload, *, timeout_ms, trace_headers):
        calls.append(replica["runtime_id"])
        if replica["runtime_id"] == "rt-a":
            raise ApiError("no_replica_available", "this runtime does not hold that version",
                           details={"runtime_id": "rt-a"})
        return reply(replica["runtime_id"])

    monkeypatch.setattr(gateway, "call_runtime", fake_call)
    result = await gateway.dispatch("v1", "m", {"request_id": "r"}, timeout_ms=5000,
                                    trace_headers={})
    assert calls == ["rt-a", "rt-b"] and result.retried and result.runtime_id == "rt-b"
    assert "rt-a" not in redis.zsets["aperture:version:v1:runtimes"]
    assert gateway._sent == {}  # every in-flight count was handed back


async def test_queue_full_is_retried_once_then_reported(redis, monkeypatch):
    await beat("rt-a", "http://a:9100", ["v1"])
    await beat("rt-b", "http://b:9100", ["v1"])
    calls = []

    async def always_full(replica, payload, *, timeout_ms, trace_headers):
        calls.append(replica["runtime_id"])
        raise ApiError("queue_full", "full", http_status=503)

    monkeypatch.setattr(gateway, "call_runtime", always_full)
    with pytest.raises(ApiError) as caught:
        await gateway.dispatch("v1", "m", {"request_id": "r"}, timeout_ms=5000,
                               trace_headers={})
    assert caught.value.code == "queue_full"
    assert len(calls) == 2 and calls[0] != calls[1]  # one retry, on the other replica
    # backpressure is not a sign the replica lost the version
    assert set(redis.zsets["aperture:version:v1:runtimes"]) == {"rt-a", "rt-b"}


async def test_a_broken_connection_after_sending_is_never_retried(redis, monkeypatch):
    await beat("rt-a", "http://a:9100", ["v1"])
    await beat("rt-b", "http://b:9100", ["v1"])
    calls = []

    async def drops_mid_request(replica, payload, *, timeout_ms, trace_headers):
        calls.append(replica["runtime_id"])
        raise ApiError("upstream_error", "the runtime connection failed",
                       details={"runtime_id": replica["runtime_id"]})

    monkeypatch.setattr(gateway, "call_runtime", drops_mid_request)
    with pytest.raises(ApiError):
        await gateway.dispatch("v1", "m", {"request_id": "r"}, timeout_ms=5000,
                               trace_headers={})
    assert len(calls) == 1  # it may have run, so it is not sent twice


async def test_a_refused_connection_is_retried_elsewhere(redis, monkeypatch):
    await beat("rt-a", "http://a:9100", ["v1"])
    await beat("rt-b", "http://b:9100", ["v1"], queues={"v1": 1})

    async def a_is_down(replica, payload, *, timeout_ms, trace_headers):
        if replica["runtime_id"] == "rt-a":
            raise ApiError("upstream_error", "could not reach the runtime",
                           details={"runtime_id": "rt-a", "connect_failed": True})
        return reply(replica["runtime_id"])

    monkeypatch.setattr(gateway, "call_runtime", a_is_down)
    result = await gateway.dispatch("v1", "m", {"request_id": "r"}, timeout_ms=5000,
                                    trace_headers={})
    assert result.runtime_id == "rt-b" and result.retried


async def test_no_retry_without_time_left(redis, monkeypatch):
    await beat("rt-a", "http://a:9100", ["v1"])
    await beat("rt-b", "http://b:9100", ["v1"])
    calls = []

    async def slow_refusal(replica, payload, *, timeout_ms, trace_headers):
        calls.append(replica["runtime_id"])
        time.sleep(0.15)
        raise ApiError("queue_full", "full", http_status=503)

    monkeypatch.setattr(gateway, "call_runtime", slow_refusal)
    with pytest.raises(ApiError):
        await gateway.dispatch("v1", "m", {"request_id": "r"}, timeout_ms=300,
                               trace_headers={})
    assert len(calls) == 1
