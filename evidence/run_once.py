import argparse
import json
import time

import httpx

BASE = "http://127.0.0.1:8205"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", type=int, default=1)
    parser.add_argument("--target-id", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--judge-kind", default="rule")
    parser.add_argument("--judge-mode", default="mock")
    parser.add_argument("--label", default="run")
    args = parser.parse_args()

    payload = {
        "dataset_id": args.dataset_id,
        "target_id": args.target_id,
        "concurrency": args.concurrency,
        "retries": args.retries,
        "timeout_s": args.timeout_s,
        "judge": {"kind": args.judge_kind, "mode": args.judge_mode},
    }
    start = time.perf_counter()
    response = httpx.post(BASE + "/api/runs", json=payload, timeout=30)
    response.raise_for_status()
    run = response.json()
    run_id = run["id"]
    print("[%s] POST /api/runs -> id=%s status=%s total=%s" % (args.label, run_id, run["status"], run["total"]))

    polls = 0
    while True:
        run = httpx.get(BASE + "/api/runs/%d" % run_id, timeout=30).json()
        if run["status"] in ("completed", "cancelled", "failed"):
            break
        polls += 1
        time.sleep(0.1)
    client_wall = time.perf_counter() - start
    metrics = run["metrics"]
    print(
        "[%s] run@%d status=%s progress=%s/%s polls=%d client_wall=%.3fs"
        % (args.label, run_id, run["status"], run["progress"]["done"], run["progress"]["total"], polls, client_wall)
    )
    print(
        "[%s] pass_rate=%.4f pass_at_1=%.4f passed=%d failed=%d errors=%d"
        % (
            args.label,
            metrics["pass_rate"],
            metrics["pass_at_1"],
            metrics["passed"],
            metrics["failed"],
            metrics["errors"],
        )
    )
    print(
        "[%s] latency_ms avg=%.1f p50=%.1f p95=%.1f | avg_steps=%.2f tokens_in=%d tokens_out=%d cost_usd=%.6f"
        % (
            args.label,
            metrics["latency_ms"]["avg"],
            metrics["latency_ms"]["p50"],
            metrics["latency_ms"]["p95"],
            metrics["avg_steps"],
            metrics["tokens_in"],
            metrics["tokens_out"],
            metrics["cost_usd"],
        )
    )
    print(
        "[%s] error_counts=%s retried_cases=%d server_span=%s..%s"
        % (args.label, json.dumps(metrics["error_counts"]), metrics["retried_cases"], run["started_at"], run["ended_at"])
    )
    print("[%s] summary=%s" % (args.label, json.dumps({"run_id": run_id, "client_wall": round(client_wall, 3), "metrics": metrics}, ensure_ascii=False)))


if __name__ == "__main__":
    main()
