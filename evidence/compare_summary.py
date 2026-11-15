import json
import pathlib

import httpx

BASE = "http://127.0.0.1:8205"
response = httpx.get(BASE + "/api/compare", params={"a": 1, "b": 2}, timeout=60)
response.raise_for_status()
data = response.json()
pathlib.Path("data/_verify/compare_1_2.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
)
summary = {
    "a_run": data["a"]["id"],
    "b_run": data["b"]["id"],
    "counts": data["counts"],
    "metric_deltas": data["metric_deltas"],
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
for key in ("fixed", "regressed", "still_failed"):
    items = data["diff"][key]
    print("\n[%s] %d cases, sample:" % (key, len(items)))
    for item in items[:2]:
        print(
            "  case=%s a=%s b=%s input=%s"
            % (item["case_id"], item["a_status"], item["b_status"], item["input"])
        )
        print("    A reason:", (item.get("judge_reason") or item.get("error") or "")[:160])
        if key in ("fixed", "regressed"):
            print("    B reason:", (item.get("b_judge_reason") or item.get("b_error") or "")[:160])
