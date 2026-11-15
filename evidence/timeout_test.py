import json

import httpx

BASE = "http://127.0.0.1:8205"
client = httpx.Client(base_url=BASE, timeout=60)

# 1) create a small dataset through the API (JSON array import)
dataset = client.post("/api/datasets", json={"name": "timeout-check", "version": "v1"}).json()
cases = [
    {"input": "超时测试用例 1：请解释什么是 Agent 评测。", "expected": "评测"},
    {"input": "超时测试用例 2：请解释 pass@1 的含义。", "expected": "pass@1"},
    {"input": "超时测试用例 3：请解释 P95 延迟的含义。", "expected": "延迟"},
]
imported = client.post(
    "/api/datasets/%d/cases" % dataset["id"], json={"cases": cases, "format": "auto"}
).json()
print("dataset=%s imported=%s skipped=%s" % (dataset["id"], imported["imported"], imported["skipped"]))

# 2) run against the deliberately slow mock target with a 0.5s timeout + 1 retry
payload = {
    "dataset_id": dataset["id"],
    "target_id": 3,  # mock-slow-timeout: latency 2000-2500ms
    "concurrency": 3,
    "retries": 1,
    "timeout_s": 0.5,
    "judge": {"kind": "rule", "mode": "mock"},
}
run = client.post("/api/runs", json=payload).json()
run_id = run["id"]
print("created run=%s config=%s" % (run_id, json.dumps(run["config"])))
while True:
    detail = client.get("/api/runs/%d" % run_id).json()
    if detail["status"] in ("completed", "cancelled", "failed"):
        break
results = client.get("/api/runs/%d/results?limit=10" % run_id).json()["items"]
print(
    "run@%s status=%s progress=%s error_counts=%s"
    % (run_id, detail["status"], detail["progress"], detail["metrics"]["error_counts"])
)
for item in results:
    print(
        "  case=%s status=%s error_type=%s attempts=%s latency_ms=%s error=%s"
        % (
            item["case_id"],
            item["status"],
            item["error_type"],
            item["attempts"],
            item["latency_ms"],
            (item["error"] or "")[:70],
        )
    )
assert detail["metrics"]["error_counts"].get("timeout") == 3, detail["metrics"]["error_counts"]
assert all(item["error_type"] == "timeout" for item in results)
assert all(item["attempts"] == 2 for item in results)
print("timeout classification OK (3/3 timeout, attempts=2 with retries=1)")
