import json
import time

import httpx

BASE = "http://127.0.0.1:8205"


def run_detail(run_id):
    return httpx.get(BASE + "/api/runs/%d" % run_id, timeout=30).json()


def result_count(run_id):
    return httpx.get(BASE + "/api/runs/%d/results?limit=1" % run_id, timeout=30).json()["total"]


payload = {
    "dataset_id": 1,
    "target_id": 1,
    "concurrency": 2,
    "retries": 0,
    "timeout_s": 30,
    "judge": {"kind": "rule", "mode": "mock"},
}
created = httpx.post(BASE + "/api/runs", json=payload, timeout=30).json()
run_id = created["id"]
print("created run=%s status=%s total=%s" % (run_id, created["status"], created["total"]))
time.sleep(0.35)
before = result_count(run_id)
started = time.perf_counter()
cancel_response = httpx.post(BASE + "/api/runs/%d/cancel" % run_id, timeout=60)
cancel_latency = time.perf_counter() - started
cancel_body = cancel_response.json()
print(
    "POST /api/runs/%d/cancel -> HTTP %d in %.2fs status=%s progress=%s"
    % (run_id, cancel_response.status_code, cancel_latency, cancel_body["status"], cancel_body["progress"])
)
# Count once the cancel call has returned, then verify it never grows again.
counts = [result_count(run_id)]
for _ in range(3):
    time.sleep(1.0)
    counts.append(result_count(run_id))
final = run_detail(run_id)
print("results count right after cancel=%s, then +1s=%s +2s=%s +3s=%s" % tuple(counts))
print(
    "final status=%s progress=%s metrics.done=%s"
    % (final["status"], final["progress"], final["metrics"].get("done"))
)
print("stable_after_cancel=%s status_ok=%s" % (len(set(counts)) == 1, final["status"] == "cancelled"))
assert len(set(counts)) == 1, counts
assert final["status"] == "cancelled"
assert final["progress"]["done"] == counts[0]
print("cancel propagation OK: status=cancelled and results count frozen at %d" % counts[0])
