"""Metric aggregation and regression diff between two runs."""

import math

# Metric definitions (mirrored in README):
# pass_rate   = passed / completed cases
# pass_at_1   = cases that passed on the first attempt / completed cases
# latency     = target call wall time, including retry backoff, excluding judging
# avg_steps   = mean agent steps over judged (pass/fail) cases
# tokens/cost = summed over every attempt recorded for the case (final attempt only)


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(math.ceil(pct / 100.0 * len(ordered))) - 1
    index = max(0, min(len(ordered) - 1, index))
    return float(ordered[index])


def compute_metrics(results, total=0):
    results = results or []
    done = len(results)
    passed = [row for row in results if row.get("status") == "pass"]
    failed = [row for row in results if row.get("status") == "fail"]
    errors = [row for row in results if row.get("status") == "error"]
    first_try = [row for row in passed if int(_num(row.get("attempts"), 1)) <= 1]
    latencies = [_num(row.get("latency_ms")) for row in results if row.get("latency_ms") is not None]
    steps = [
        int(_num(row.get("steps")))
        for row in results
        if row.get("status") in ("pass", "fail") and row.get("steps") is not None
    ]
    tokens_in = sum(int(_num(row.get("tokens_in"))) for row in results)
    tokens_out = sum(int(_num(row.get("tokens_out"))) for row in results)
    cost = sum(_num(row.get("cost_usd")) for row in results)
    error_counts = {}
    for row in errors:
        key = row.get("error_type") or "unknown"
        error_counts[key] = error_counts.get(key, 0) + 1
    retried = sum(1 for row in results if int(_num(row.get("attempts"), 1)) > 1)
    denominator = done
    return {
        "total": int(total or done),
        "done": done,
        "passed": len(passed),
        "failed": len(failed),
        "errors": len(errors),
        "pass_rate": round(len(passed) / denominator, 4) if denominator else 0.0,
        "pass_at_1": round(len(first_try) / denominator, 4) if denominator else 0.0,
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(percentile(latencies, 95), 1),
        },
        "avg_steps": round(sum(steps) / len(steps), 2) if steps else 0.0,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "cost_usd": round(cost, 6),
        "error_counts": error_counts,
        "retried_cases": retried,
    }


def _judge_reason(result):
    if not result:
        return None
    judge = result.get("judge")
    if isinstance(judge, dict):
        return judge.get("reason")
    if isinstance(judge, str) and judge:
        import json

        try:
            parsed = json.loads(judge)
            if isinstance(parsed, dict):
                return parsed.get("reason")
        except (TypeError, ValueError):
            return None
    return None


def _truncate(text, limit=4000):
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "... (truncated)"


def _diff_item(result):
    if result is None:
        return None
    return {
        "case_id": result.get("case_id"),
        "input": result.get("case_input"),
        "expected": result.get("case_expected"),
        "status": result.get("status"),
        "latency_ms": result.get("latency_ms"),
        "output": _truncate(result.get("output")),
        "error": result.get("error"),
        "error_type": result.get("error_type"),
        "judge_reason": _judge_reason(result),
        "judge": result.get("judge"),
    }


def _delta(a, b, digits=4):
    return {"a": a, "b": b, "delta": round(b - a, digits)}


def build_compare(run_a, run_b, results_a, results_b, limit=200):
    metrics_a = compute_metrics(results_a, run_a.get("total") or 0)
    metrics_b = compute_metrics(results_b, run_b.get("total") or 0)
    map_a = {row["case_id"]: row for row in results_a}
    map_b = {row["case_id"]: row for row in results_b}

    categories = {
        "fixed": [],
        "regressed": [],
        "still_failed": [],
        "still_passed": [],
        "new_case": [],
        "removed_case": [],
    }
    for case_id, row_b in map_b.items():
        row_a = map_a.get(case_id)
        if row_a is None:
            item = _diff_item(row_b)
            item["a_status"] = None
            item["b_status"] = row_b.get("status")
            categories["new_case"].append(item)
            continue
        a_pass = row_a.get("status") == "pass"
        b_pass = row_b.get("status") == "pass"
        if a_pass and b_pass:
            key = "still_passed"
        elif a_pass and not b_pass:
            key = "regressed"
        elif not a_pass and b_pass:
            key = "fixed"
        else:
            key = "still_failed"
        item = _diff_item(row_a)
        item["a_status"] = row_a.get("status")
        item["b_status"] = row_b.get("status")
        item["b_output"] = _truncate(row_b.get("output"))
        item["b_error"] = row_b.get("error")
        item["b_judge_reason"] = _judge_reason(row_b)
        categories[key].append(item)

    for case_id, row_a in map_a.items():
        if case_id not in map_b:
            item = _diff_item(row_a)
            item["a_status"] = row_a.get("status")
            item["b_status"] = None
            categories["removed_case"].append(item)

    counts = {key: len(value) for key, value in categories.items()}
    for key, value in categories.items():
        categories[key] = value[:limit]

    latency_a = metrics_a["latency_ms"]
    latency_b = metrics_b["latency_ms"]
    return {
        "a": {
            "id": run_a.get("id"),
            "dataset_id": run_a.get("dataset_id"),
            "target_id": run_a.get("target_id"),
            "status": run_a.get("status"),
            "total": run_a.get("total"),
            "started_at": run_a.get("started_at"),
            "ended_at": run_a.get("ended_at"),
        },
        "b": {
            "id": run_b.get("id"),
            "dataset_id": run_b.get("dataset_id"),
            "target_id": run_b.get("target_id"),
            "status": run_b.get("status"),
            "total": run_b.get("total"),
            "started_at": run_b.get("started_at"),
            "ended_at": run_b.get("ended_at"),
        },
        "metrics": {"a": metrics_a, "b": metrics_b},
        "metric_deltas": {
            "pass_rate": _delta(metrics_a["pass_rate"], metrics_b["pass_rate"]),
            "pass_at_1": _delta(metrics_a["pass_at_1"], metrics_b["pass_at_1"]),
            "latency_avg_ms": _delta(latency_a["avg"], latency_b["avg"], 1),
            "latency_p50_ms": _delta(latency_a["p50"], latency_b["p50"], 1),
            "latency_p95_ms": _delta(latency_a["p95"], latency_b["p95"], 1),
            "avg_steps": _delta(metrics_a["avg_steps"], metrics_b["avg_steps"], 2),
            "tokens_total": _delta(metrics_a["tokens_total"], metrics_b["tokens_total"], 0),
            "cost_usd": _delta(metrics_a["cost_usd"], metrics_b["cost_usd"], 6),
        },
        "counts": counts,
        "diff": categories,
        "limit": limit,
    }
