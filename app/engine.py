"""Async batch execution engine.

One run = fan out every active case of a dataset as an asyncio task guarded by
a ``Semaphore``.  Each case gets a per-call timeout and exponential-backoff
retries.  A run can be cancelled cooperatively (event, checked between attempts)
and hard-cancelled (asyncio task cancellation); after cancellation no further
results are written and the run status becomes ``cancelled``.
"""

import asyncio
import time

import httpx

from . import db, judge, metrics, targets

RUN_TASKS = {}
RUN_CANCELS = {}


def is_active(run_id):
    task = RUN_TASKS.get(run_id)
    return task is not None and not task.done()


def schedule(run_id, dataset_id, target_id, config):
    """Start a run in the background and return immediately."""
    cancel_event = asyncio.Event()
    RUN_CANCELS[run_id] = cancel_event
    task = asyncio.create_task(run_engine(run_id, dataset_id, target_id, config, cancel_event))
    RUN_TASKS[run_id] = task
    task.add_done_callback(lambda finished: _cleanup(run_id, finished))
    return task


def _cleanup(run_id, task):
    RUN_TASKS.pop(run_id, None)
    RUN_CANCELS.pop(run_id, None)
    if not task.cancelled():
        try:
            task.exception()
        except Exception:
            pass


async def cancel_run(run_id, wait_s=15.0):
    """Cancel a run and wait until it stops writing results."""
    event = RUN_CANCELS.get(run_id)
    if event is not None:
        event.set()
    task = RUN_TASKS.get(run_id)
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_s)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            pass
    row = db.query_one("SELECT status FROM runs WHERE id=?", (run_id,))
    if row and row["status"] not in ("completed", "cancelled", "failed"):
        _finalize(run_id, "cancelled")


def _finalize(run_id, status, error=None):
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None:
        return
    results = db.query("SELECT * FROM results WHERE run_id=? ORDER BY id", (run_id,))
    final_metrics = metrics.compute_metrics(results, int(run.get("total") or len(results)))
    if status == "cancelled":
        final_metrics["cancelled"] = True
    db.execute(
        "UPDATE runs SET status=?, metrics=?, error=?, ended_at=? WHERE id=?",
        (status, db.dumps(final_metrics), error, db.now_iso(), run_id),
    )


async def run_engine(run_id, dataset_id, target_id, config, cancel_event):
    try:
        await _run(run_id, dataset_id, target_id, config, cancel_event)
    except asyncio.CancelledError:
        _finalize(run_id, "cancelled")
        return
    except Exception as exc:  # engine-level failure
        _finalize(run_id, "failed", error="%s: %s" % (type(exc).__name__, exc))


async def _run(run_id, dataset_id, target_id, config, cancel_event):
    target = db.query_one("SELECT * FROM targets WHERE id=?", (target_id,))
    if target is None:
        raise RuntimeError("target %s not found" % target_id)
    cases = db.query(
        'SELECT id, "input", expected, tags, meta FROM cases WHERE dataset_id=? AND active=1 ORDER BY id',
        (dataset_id,),
    )
    db.execute(
        "UPDATE runs SET status='running', started_at=?, total=? WHERE id=?",
        (db.now_iso(), len(cases), run_id),
    )
    target_config = db.loads(target.get("config"), {}) or {}
    judge_config = config.get("judge") or {"kind": "rule", "mode": "mock"}
    concurrency = max(1, int(config.get("concurrency", 4)))
    retries = max(0, int(config.get("retries", 1)))
    timeout_s = float(config.get("timeout_s", 30))

    semaphore = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(
        max_connections=concurrency + 4,
        max_keepalive_connections=concurrency + 4,
    )
    async with httpx.AsyncClient(limits=limits) as client:
        async def worker(case):
            if cancel_event.is_set():
                return
            async with semaphore:
                if cancel_event.is_set():
                    return
                await _process_case(
                    run_id=run_id,
                    case=case,
                    target=target,
                    target_config=target_config,
                    judge_config=judge_config,
                    timeout_s=timeout_s,
                    retries=retries,
                    cancel_event=cancel_event,
                    client=client,
                )

        tasks = [asyncio.create_task(worker(case)) for case in cases]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    if cancel_event.is_set():
        _finalize(run_id, "cancelled")
        return
    _finalize(run_id, "completed")


async def _process_case(
    run_id,
    case,
    target,
    target_config,
    judge_config,
    timeout_s,
    retries,
    cancel_event,
    client,
):
    started = time.perf_counter()
    latency_ms = 0.0
    output = None
    attempts = 0
    last_error = None
    target_output = None

    while attempts <= retries:
        if cancel_event.is_set():
            return
        attempts += 1
        try:
            target_output = await asyncio.wait_for(
                targets.call_target(target["kind"], target_config, case, timeout_s, client),
                timeout=timeout_s,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            last_error = None
            break
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - started) * 1000.0
            last_error = targets.TargetError("timeout", "case timed out after %ss" % timeout_s)
        except targets.TargetError as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            last_error = exc
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            last_error = targets.TargetError(
                "target_error", "%s: %s" % (type(exc).__name__, exc), retryable=False
            )
        if attempts > retries or not last_error.retryable:
            break
        backoff = min(2.0, 0.2 * (2 ** (attempts - 1)))
        await asyncio.sleep(backoff)
        if cancel_event.is_set():
            return

    if last_error is not None:
        _store_result(
            run_id=run_id,
            case=case,
            status="error",
            latency_ms=latency_ms,
            attempts=attempts,
            output="",
            error=str(last_error),
            error_type=last_error.category,
            judge_result=None,
        )
        return

    try:
        judge_result = await judge.judge_case(judge_config, case, target_output.output, client)
    except judge.JudgeError as exc:
        _store_result(
            run_id=run_id,
            case=case,
            status="error",
            latency_ms=latency_ms,
            attempts=attempts,
            output=target_output.output,
            error=str(exc),
            error_type=getattr(exc, "category", "judge_error"),
            judge_result=None,
        )
        return

    _store_result(
        run_id=run_id,
        case=case,
        status="pass" if judge_result.get("passed") else "fail",
        latency_ms=latency_ms,
        attempts=attempts,
        output=target_output.output,
        error=None,
        error_type=None,
        judge_result=judge_result,
        target_output=target_output,
    )


def _store_result(
    run_id,
    case,
    status,
    latency_ms,
    attempts,
    output,
    error,
    error_type,
    judge_result,
    target_output=None,
):
    tokens_in = target_output.tokens_in if target_output else 0
    tokens_out = target_output.tokens_out if target_output else 0
    steps = target_output.steps if target_output else 0
    cost = target_output.cost_usd if target_output else 0.0
    db.execute(
        'INSERT INTO results (run_id, case_id, status, latency_ms, steps, tokens_in, tokens_out, '
        'cost_usd, attempts, output, error, error_type, judge, created_at) '
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            case["id"],
            status,
            round(latency_ms, 1),
            int(steps or 0),
            int(tokens_in or 0),
            int(tokens_out or 0),
            float(cost or 0.0),
            int(attempts or 1),
            "" if output is None else str(output)[: targets.MAX_OUTPUT_CHARS],
            error,
            error_type,
            db.dumps(judge_result) if judge_result is not None else None,
            db.now_iso(),
        ),
    )
