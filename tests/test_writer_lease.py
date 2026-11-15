import asyncio
import threading

from fastapi.testclient import TestClient

from ymir.lens_answers import answer_for
from ymir.writer_lease import WriterLease


class FakeDict(dict):
    """The subset of modal.Dict the lease uses."""
    def put(self, key, value, skip_if_exists=False):
        if skip_if_exists and key in self:
            return False
        self[key] = value
        return True


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_second_container_waits_for_the_holder():
    store, clock = FakeDict(), Clock()
    old, new = WriterLease(store, "old", clock=clock), WriterLease(store, "new", clock=clock)
    assert old.try_acquire() and old.held.is_set()
    assert not new.try_acquire() and not new.held.is_set()
    clock.now += 30
    assert old.renew()
    clock.now += 45  # within the TTL of the renewed heartbeat
    assert not new.try_acquire()


def test_released_lease_passes_to_the_next_container():
    store, clock = FakeDict(), Clock()
    old, new = WriterLease(store, "old", clock=clock), WriterLease(store, "new", clock=clock)
    old.try_acquire()
    old.ready.set()
    old.release()
    assert not old.held.is_set() and not old.ready.is_set()
    assert new.try_acquire()


def test_lapsed_holder_is_taken_over_and_stops_writing():
    store, clock = FakeDict(), Clock()
    old, new = WriterLease(store, "old", clock=clock), WriterLease(store, "new", clock=clock)
    old.try_acquire()
    old.ready.set()
    clock.now += 61  # the old container stopped heartbeating
    assert new.try_acquire()
    assert not old.renew()
    assert not old.held.is_set() and not old.ready.is_set()
    old.release()  # must not free the new holder's lease
    assert store["writer"]["owner"] == "new"


def test_run_allows_writes_only_after_take_over_succeeds():
    store, stop = FakeDict(), threading.Event()
    lease = WriterLease(store, "a", beat=0.01, wait=0.01)
    seen = []

    def take_over():
        seen.append(lease.ready.is_set())  # still refusing writes while reloading state
        stop.set()

    lease.run(take_over, lambda: None, stop)
    assert seen == [False] and lease.ready.is_set()


def test_failed_take_over_releases_the_lease():
    store, stop = FakeDict(), threading.Event()
    lease = WriterLease(store, "a", beat=0.01, wait=0.01)

    def take_over():
        stop.set()
        raise RuntimeError("volume reload failed")

    lease.run(take_over, lambda: None, stop)
    assert not lease.held.is_set() and not lease.ready.is_set() and "writer" not in store


def test_write_gate_refuses_writes_and_allows_reads():
    from ymir.app import app
    client = TestClient(app)
    app.state.write_gate = lambda: "Ymir is finishing an update. Try again in a minute."
    try:
        res = client.post("/api/reviews", json={})
        assert res.status_code == 503 and res.headers["retry-after"] == "30"
        assert res.json()["detail"].startswith("Ymir is finishing an update")
        assert client.get("/api/library").status_code == 200
    finally:
        del app.state.write_gate


def test_job_stream_is_async_and_ends_with_the_answer(tmp_path):
    from ymir.app import documents, store
    from ymir.lens_jobs import JobStore
    jobs = JobStore(store, documents, root=tmp_path / "lens", runner=lambda *a, **k: {})
    with jobs.db() as db:
        db.execute("INSERT INTO jobs (id,request_id,signature,project,conversation,request,manifest,status,created) "
                   "VALUES ('j','r','s','p','c','{}','{}','queued','now')")
        jobs.event("j", {"type": "job", "status": "queued"}, db)

    async def read():
        lines = []
        async for line in jobs.stream("j", poll=0.01):
            lines.append(line)
            if len(lines) == 1:
                with jobs.db() as db:
                    jobs._terminal("j", "completed", {"status": "answered", "text": "ok", "citations": []}, db)
        return lines

    lines = asyncio.run(read())
    assert [l.split('"type": "')[1].split('"')[0] for l in lines] == ["job", "job", "answer"]


def test_unknown_page_keeps_the_answer_without_a_review():
    result = {"status": "needs_review",
              "counts": [{"page_id": "derived", "object_class": "door", "count": 2},
                         {"page_id": "page", "object_class": "window", "count": 1}],
              "rooms": [{"page_id": "derived", "candidate_id": "r1", "room_id": "r1", "polygon": [[0, 0], [1, 0], [1, 1]]}],
              "proposals": [], "accepted_proposal_ids": []}
    answer = answer_for("job", result, {"page": "sheet"})
    assert [c["sheet"] for c in answer["counts"]] == [None, "sheet"]
    assert answer["rooms"][0]["sheet"] is None
    assert [r["sheet"] for r in answer["reviews"]] == ["sheet"]
