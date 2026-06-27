import os
import queue

from optimizer.preprune_ranker import SharedPrePruneGpuService, initialize_preprune_worker
from tools.device_utils import cuda_available


class BrokenQueue:
    def get(self, *args, **kwargs):
        raise EOFError("manager queue closed")

    def empty(self):
        return True

    def qsize(self):
        return 0


class BrokenMap(dict):
    def __setitem__(self, key, value):
        raise BrokenPipeError("manager response map closed")


def test_shared_ranker_contains_manager_queue_failure():
    service = SharedPrePruneGpuService(BrokenQueue(), {}, enabled=False)
    assert service._receive_batch() == []
    snapshot = service.snapshot()
    assert snapshot["service_failed"] is True
    assert any("manager queue closed" in error for error in snapshot["errors"])


def test_shared_ranker_contains_manager_response_failure():
    service = SharedPrePruneGpuService(BrokenQueue(), BrokenMap(), enabled=False)
    service._respond("token", {"indices": []})
    snapshot = service.snapshot()
    assert snapshot["service_failed"] is True
    assert any("manager response map closed" in error for error in snapshot["errors"])


def test_shared_ranker_stops_waiting_at_useful_batch_target():
    requests = queue.Queue()
    requests.put(("one", "memory-one", 6, 16, 3, "proposal"))
    requests.put(("two", "memory-two", 5, 16, 3, "proposal"))
    service = SharedPrePruneGpuService(
        requests, {}, enabled=False, batch_size=10, max_batch_rows=40, fill_timeout=0.01,
    )
    batch = service._receive_batch()
    assert len(batch) == 2
    assert sum(int(item[2]) for item in batch) == 11


def test_shared_ranker_start_is_idempotent_after_initialization():
    service = SharedPrePruneGpuService(queue.Queue(), {}, enabled=False)
    service.enabled = True
    service._thread = object()
    service._initialized = True
    service._cuda_initialization_count = 1
    service._thread_start_count = 1
    service.start()
    service.start()
    snapshot = service.snapshot()
    assert snapshot["start_calls"] == 2
    assert snapshot["thread_start_count"] == 1
    assert snapshot["cuda_initialization_count"] == 1


def test_cpu_worker_initializer_hides_cuda(monkeypatch):
    monkeypatch.delenv("SURVIVOR_OPTIMIZER_CPU_WORKER", raising=False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    initialize_preprune_worker(None, None, False)
    assert os.environ["SURVIVOR_OPTIMIZER_CPU_WORKER"] == "1"
    assert os.environ["CUDA_VISIBLE_DEVICES"] == ""
    assert cuda_available() is False


def test_ranker_close_fails_queued_requests_and_releases_cleanly():
    requests = queue.Queue()
    responses = {}
    requests.put(("queued", "unused-memory", 4, 16, 2, "proposal"))
    service = SharedPrePruneGpuService(requests, responses, enabled=False)
    service._cuda_released = True
    snapshot = service.close()
    assert "fatal_error" in responses["queued"]
    assert snapshot["cuda_released"] is True
