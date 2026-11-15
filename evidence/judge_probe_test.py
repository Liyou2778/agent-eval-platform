import json

import httpx

BASE = "http://127.0.0.1:8205"
client = httpx.Client(base_url=BASE, timeout=30)

# probe the offline mock target
probe_mock = client.post("/api/targets/1/probe", json={"input": "水的化学式是什么？"}).json()
print("probe mock target:", json.dumps(probe_mock, ensure_ascii=False))
assert probe_mock["ok"] is True

# probe the generic http target: body template {"prompt": "{input}"} -> /api/echo,
# response mapping answer/steps/tokens/cost
probe_http = client.post("/api/targets/4/probe", json={"input": "ping"}).json()
print("probe http target:", json.dumps(probe_http, ensure_ascii=False))
assert probe_http["ok"] is True
assert probe_http["output"] == "echo: ping", probe_http
assert probe_http["steps"] == 3 and probe_http["tokens_in"] == 4 and probe_http["tokens_out"] == 10

# probe the openai-compatible target without a key: must fail gracefully, offline
probe_openai = client.post("/api/targets/5/probe", json={"input": "ping"}).json()
print("probe openai target:", json.dumps(probe_openai, ensure_ascii=False))
assert probe_openai["ok"] is False and probe_openai["error_type"] == "config_error"

# a real run through the http target on the 3-case dataset (echo output contains
# the expected keywords because they appear in the input)
payload = {
    "dataset_id": 2,
    "target_id": 4,
    "concurrency": 2,
    "retries": 0,
    "timeout_s": 10,
    "judge": {"kind": "rule", "mode": "mock"},
}
run = client.post("/api/runs", json=payload).json()
run_id = run["id"]
while True:
    detail = client.get("/api/runs/%d" % run_id).json()
    if detail["status"] in ("completed", "cancelled", "failed"):
        break
metrics = detail["metrics"]
print(
    "http-target run=%s status=%s pass_rate=%s steps_avg=%s tokens_in=%s tokens_out=%s cost=%.8f"
    % (
        run_id,
        detail["status"],
        metrics["pass_rate"],
        metrics["avg_steps"],
        metrics["tokens_in"],
        metrics["tokens_out"],
        metrics["cost_usd"],
    )
)
assert metrics["pass_rate"] == 1.0 and metrics["avg_steps"] == 3.0
assert metrics["tokens_in"] > 0 and metrics["tokens_out"] > 0

# run 200 cases with LLM-as-judge in offline mock mode
payload = {
    "dataset_id": 1,
    "target_id": 2,
    "concurrency": 8,
    "retries": 1,
    "timeout_s": 30,
    "judge": {"kind": "llm", "mode": "mock"},
}
run = client.post("/api/runs", json=payload).json()
run_id = run["id"]
while True:
    detail = client.get("/api/runs/%d" % run_id).json()
    if detail["status"] in ("completed", "cancelled", "failed"):
        break
print(
    "llm-mock judge run=%s status=%s pass_rate=%s passed=%s failed=%s"
    % (
        run_id,
        detail["status"],
        detail["metrics"]["pass_rate"],
        detail["metrics"]["passed"],
        detail["metrics"]["failed"],
    )
)
failures = client.get("/api/runs/%d/results?status=failed&limit=2" % run_id).json()
sample = failures["items"][0]
print("failed case:", sample["case"]["input"])
print("  expected:", sample["case"]["expected"])
print("  output:", sample["output"])
print("  judge.kind/mode:", sample["judge"]["kind"], sample["judge"]["mode"])
print("  judge.reason:", sample["judge"]["reason"])
assert sample["judge"]["kind"] == "llm" and sample["judge"]["mode"] == "mock"
print("targets probes + http run + llm mock judge OK")
