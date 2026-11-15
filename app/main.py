"""FastAPI application: REST API + single page dashboard."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db, engine, metrics, targets
from .models import CasesImport, DatasetCreate, ProbeRequest, RunCreate, TargetCreate

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
MAX_LIST_LIMIT = 1000


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    stale = db.query("SELECT id FROM runs WHERE status IN ('queued','running')")
    if stale:
        db.execute(
            "UPDATE runs SET status='failed', error=?, ended_at=? WHERE status IN ('queued','running')",
            ("interrupted by server restart", db.now_iso()),
        )
    yield
    tasks = [task for task in engine.RUN_TASKS.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(
    title="Agent Evaluation Platform",
    version=__version__,
    description="Offline-first agent evaluation platform: datasets x targets x batch runs x metrics x judge x regression diff.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# serializers
# ---------------------------------------------------------------------------

def _split_tags(value):
    return [part for part in (value or "").split(",") if part]


def _dataset_out(row, case_count=None):
    out = {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "created_at": row["created_at"],
    }
    if case_count is not None:
        out["case_count"] = case_count
    return out


def _case_out(row):
    return {
        "id": row["id"],
        "dataset_id": row["dataset_id"],
        "input": row["input"],
        "expected": row["expected"],
        "tags": _split_tags(row.get("tags")),
        "meta": db.loads(row.get("meta"), {}) or {},
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def _target_out(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "config": db.loads(row.get("config"), {}) or {},
        "created_at": row["created_at"],
    }


def _run_out(row):
    run_id = row["id"]
    results_count = db.scalar("SELECT COUNT(*) FROM results WHERE run_id=?", (run_id,)) or 0
    total = int(row.get("total") or 0)
    stored = db.loads(row.get("metrics"), {}) or {}
    if row["status"] in ("completed", "cancelled", "failed") and stored:
        run_metrics = stored
    else:
        result_rows = db.query("SELECT * FROM results WHERE run_id=?", (run_id,))
        run_metrics = metrics.compute_metrics(result_rows, total or len(result_rows))
    percent = round(100.0 * results_count / total, 1) if total else 0.0
    return {
        "id": run_id,
        "dataset_id": row["dataset_id"],
        "dataset_name": row.get("dataset_name"),
        "target_id": row["target_id"],
        "target_name": row.get("target_name"),
        "status": row["status"],
        "total": total,
        "progress": {"done": results_count, "total": total, "percent": percent},
        "config": db.loads(row.get("config"), {}) or {},
        "metrics": run_metrics,
        "error": row.get("error"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "created_at": row.get("created_at"),
    }


def _result_out(row):
    judge_data = db.loads(row.get("judge"), None)
    item = {
        "id": row["id"],
        "run_id": row["run_id"],
        "case_id": row["case_id"],
        "status": row["status"],
        "latency_ms": row["latency_ms"],
        "steps": row["steps"],
        "tokens_in": row["tokens_in"],
        "tokens_out": row["tokens_out"],
        "cost_usd": row["cost_usd"],
        "attempts": row["attempts"],
        "output": row.get("output"),
        "error": row.get("error"),
        "error_type": row.get("error_type"),
        "judge": judge_data,
        "created_at": row.get("created_at"),
    }
    if "case_input" in row:
        item["case"] = {
            "input": row.get("case_input"),
            "expected": row.get("case_expected"),
            "tags": _split_tags(row.get("case_tags")),
        }
    return item


def _require_dataset(dataset_id):
    row = db.query_one("SELECT * FROM datasets WHERE id=?", (dataset_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="dataset %s not found" % dataset_id)
    return row


def _require_target(target_id):
    row = db.query_one("SELECT * FROM targets WHERE id=?", (target_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="target %s not found" % target_id)
    return row


def _require_run(run_id):
    row = db.query_one(
        "SELECT r.*, d.name AS dataset_name, t.name AS target_name FROM runs r "
        "LEFT JOIN datasets d ON d.id=r.dataset_id LEFT JOIN targets t ON t.id=r.target_id WHERE r.id=?",
        (run_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="run %s not found" % run_id)
    return row


# ---------------------------------------------------------------------------
# health / stats
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    db.scalar("SELECT 1")
    return {
        "status": "ok",
        "service": "agent-eval-platform",
        "version": __version__,
        "time": db.now_iso(),
        "database": str(db.resolve_db_path()),
    }


@app.get("/api/stats")
def stats():
    runs_by_status = {
        row["status"]: row["count"]
        for row in db.query("SELECT status, COUNT(*) AS count FROM runs GROUP BY status")
    }
    return {
        "datasets": db.scalar("SELECT COUNT(*) FROM datasets") or 0,
        "cases": db.scalar("SELECT COUNT(*) FROM cases") or 0,
        "targets": db.scalar("SELECT COUNT(*) FROM targets") or 0,
        "runs": db.scalar("SELECT COUNT(*) FROM runs") or 0,
        "results": db.scalar("SELECT COUNT(*) FROM results") or 0,
        "runs_by_status": runs_by_status,
    }


@app.post("/api/echo")
async def echo(payload: dict[str, Any]):
    """Tiny echo endpoint used to exercise generic http targets offline."""
    prompt = payload.get("prompt")
    if prompt is None:
        prompt = payload.get("input")
    if prompt is None:
        prompt = json.dumps(payload, ensure_ascii=False)
    prompt = str(prompt)
    answer = "echo: " + prompt
    tokens_in = max(1, len(prompt))
    tokens_out = max(1, len(answer))
    return {
        "answer": answer,
        "steps": 3,
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
        "cost_usd": round((tokens_in + tokens_out) * 1e-6, 8),
    }


# ---------------------------------------------------------------------------
# datasets + cases
# ---------------------------------------------------------------------------

@app.get("/api/datasets")
def list_datasets():
    rows = db.query(
        "SELECT d.*, (SELECT COUNT(*) FROM cases c WHERE c.dataset_id=d.id) AS case_count "
        "FROM datasets d ORDER BY d.id DESC"
    )
    return {"items": [_dataset_out(row, row["case_count"]) for row in rows]}


@app.post("/api/datasets", status_code=201)
def create_dataset(payload: DatasetCreate):
    dataset_id = db.execute(
        "INSERT INTO datasets (name, version, created_at) VALUES (?,?,?)",
        (payload.name.strip(), payload.version.strip() or "v1", db.now_iso()),
    )
    row = _require_dataset(dataset_id)
    return _dataset_out(row, 0)


def _parse_cases_text(text, fmt):
    """Parse a JSON array or JSONL text into a list of case objects."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty import text")
    errors = []
    items = None
    if fmt in ("auto", "json"):
        try:
            parsed = json.loads(text)
        except ValueError:
            if fmt == "json":
                raise ValueError("invalid JSON: expected an array of case objects")
        else:
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, dict) and isinstance(parsed.get("cases"), list):
                items = parsed["cases"]
            elif isinstance(parsed, dict):
                items = [parsed]
            else:
                raise ValueError("JSON import must be an array or an object with a cases array")
    if items is None:
        items = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                errors.append({"line": line_no, "error": "invalid JSON: %s" % exc})
                continue
            if not isinstance(item, dict):
                errors.append({"line": line_no, "error": "line is not a JSON object"})
                continue
            items.append(item)
    return items, errors


def _normalize_case(item):
    if not isinstance(item, dict):
        raise ValueError("case must be a JSON object")
    value = item.get("input")
    if value is None:
        value = item.get("question")
    if value is None or str(value).strip() == "":
        raise ValueError("case.input is required")
    input_text = str(value)
    expected = item.get("expected", "")
    if expected is None:
        expected = ""
    elif not isinstance(expected, str):
        expected = json.dumps(expected, ensure_ascii=False)
    tags = item.get("tags", [])
    if isinstance(tags, list):
        tags = ",".join(str(part).strip() for part in tags if str(part).strip())
    else:
        tags = str(tags or "").strip()
    meta = item.get("meta", {})
    if not isinstance(meta, dict):
        meta = {"value": meta}
    active = 1 if item.get("active", True) else 0
    return input_text, expected, tags, json.dumps(meta, ensure_ascii=False), active


@app.post("/api/datasets/{dataset_id}/cases", status_code=201)
def import_cases(dataset_id: int, payload: CasesImport):
    _require_dataset(dataset_id)
    errors = []
    if payload.cases is not None:
        raw_items = payload.cases
    else:
        try:
            raw_items, errors = _parse_cases_text(payload.text or "", payload.format)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    now = db.now_iso()
    rows = []
    for index, item in enumerate(raw_items):
        try:
            input_text, expected, tags, meta, active = _normalize_case(item)
        except ValueError as exc:
            errors.append({"index": index, "error": str(exc)})
            continue
        rows.append((dataset_id, input_text, expected, tags, meta, active, now))
    if rows:
        db.executemany(
            'INSERT INTO cases (dataset_id, "input", expected, tags, meta, active, created_at) '
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    total = db.scalar("SELECT COUNT(*) FROM cases WHERE dataset_id=?", (dataset_id,)) or 0
    return {
        "dataset_id": dataset_id,
        "imported": len(rows),
        "skipped": len(errors),
        "errors": errors[:20],
        "total_cases": total,
    }


@app.get("/api/datasets/{dataset_id}/cases")
def list_cases(
    dataset_id: int,
    limit: int = Query(100, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    active: int | None = Query(default=None, ge=0, le=1),
):
    _require_dataset(dataset_id)
    where = "dataset_id=?"
    params = [dataset_id]
    if active is not None:
        where += " AND active=?"
        params.append(active)
    total = db.scalar("SELECT COUNT(*) FROM cases WHERE " + where, tuple(params)) or 0
    rows = db.query(
        "SELECT * FROM cases WHERE " + where + " ORDER BY id LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    )
    return {"total": total, "limit": limit, "offset": offset, "items": [_case_out(row) for row in rows]}


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------

@app.get("/api/targets")
def list_targets():
    rows = db.query("SELECT * FROM targets ORDER BY id DESC")
    return {"items": [_target_out(row) for row in rows]}


@app.post("/api/targets", status_code=201)
def create_target(payload: TargetCreate):
    config = dict(payload.config or {})
    if payload.kind == "http" and not config.get("url"):
        raise HTTPException(status_code=400, detail="http target requires config.url")
    target_id = db.execute(
        "INSERT INTO targets (name, kind, config, created_at) VALUES (?,?,?,?)",
        (payload.name.strip(), payload.kind, db.dumps(config), db.now_iso()),
    )
    return _target_out(_require_target(target_id))


@app.post("/api/targets/{target_id}/probe")
async def probe_target(target_id: int, payload: ProbeRequest | None = None):
    row = _require_target(target_id)
    config = db.loads(row.get("config"), {}) or {}
    case_input = payload.input if payload is not None else "你好，请用一句话介绍你自己。"
    case = {"id": 0, "input": case_input, "expected": ""}
    timeout_s = float(config.get("timeout_s") or 30)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient() as client:
            output = await asyncio.wait_for(
                targets.call_target(row["kind"], config, case, timeout_s, client),
                timeout=timeout_s,
            )
        return {
            "ok": True,
            "target_id": target_id,
            "kind": row["kind"],
            "output": output.output,
            "steps": output.steps,
            "tokens_in": output.tokens_in,
            "tokens_out": output.tokens_out,
            "cost_usd": round(output.cost_usd, 8),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "target_id": target_id,
            "kind": row["kind"],
            "error_type": "timeout",
            "error": "probe timed out after %ss" % timeout_s,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
        }
    except targets.TargetError as exc:
        return {
            "ok": False,
            "target_id": target_id,
            "kind": row["kind"],
            "error_type": exc.category,
            "error": str(exc),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
        }


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

@app.get("/api/runs")
def list_runs(
    limit: int = Query(50, ge=1, le=MAX_LIST_LIMIT),
    status: str | None = Query(default=None),
):
    sql = (
        "SELECT r.*, d.name AS dataset_name, t.name AS target_name FROM runs r "
        "LEFT JOIN datasets d ON d.id=r.dataset_id LEFT JOIN targets t ON t.id=r.target_id"
    )
    params = []
    if status:
        sql += " WHERE r.status=?"
        params.append(status)
    sql += " ORDER BY r.id DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    return {"items": [_run_out(row) for row in rows]}


@app.post("/api/runs", status_code=201)
async def create_run(payload: RunCreate):
    _require_dataset(payload.dataset_id)
    _require_target(payload.target_id)
    judge_config = dict(payload.judge or {})
    kind = str(judge_config.get("kind") or "rule").lower()
    if kind not in ("rule", "llm"):
        raise HTTPException(status_code=400, detail="judge.kind must be 'rule' or 'llm'")
    if kind == "llm" and str(judge_config.get("mode") or "mock").lower() not in ("mock", "openai"):
        raise HTTPException(status_code=400, detail="judge.mode must be 'mock' or 'openai'")
    total = (
        db.scalar(
            "SELECT COUNT(*) FROM cases WHERE dataset_id=? AND active=1",
            (payload.dataset_id,),
        )
        or 0
    )
    if total == 0:
        raise HTTPException(status_code=400, detail="dataset has no active cases")
    config = {
        "concurrency": payload.concurrency,
        "retries": payload.retries,
        "timeout_s": payload.timeout_s,
        "judge": judge_config,
    }
    run_id = db.execute(
        "INSERT INTO runs (dataset_id, target_id, status, config, metrics, total, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (payload.dataset_id, payload.target_id, "queued", db.dumps(config), "{}", total, db.now_iso()),
    )
    engine.schedule(run_id, payload.dataset_id, payload.target_id, config)
    return _run_out(_require_run(run_id))


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    return _run_out(_require_run(run_id))


@app.get("/api/runs/{run_id}/results")
def list_results(
    run_id: int,
    status: str | None = Query(default=None),
    limit: int = Query(100, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(0, ge=0),
):
    _require_run(run_id)
    where = "r.run_id=?"
    params = [run_id]
    if status == "failed":
        where += " AND r.status IN ('fail','error')"
    elif status:
        where += " AND r.status=?"
        params.append(status)
    total = db.scalar("SELECT COUNT(*) FROM results r WHERE " + where, tuple(params)) or 0
    rows = db.query(
        "SELECT r.*, c.input AS case_input, c.expected AS case_expected, c.tags AS case_tags "
        "FROM results r LEFT JOIN cases c ON c.id=r.case_id WHERE " + where + " "
        "ORDER BY r.id LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    )
    return {
        "run_id": run_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_result_out(row) for row in rows],
    }


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run_endpoint(run_id: int):
    row = _require_run(run_id)
    if row["status"] in ("completed", "cancelled", "failed"):
        return _run_out(row)
    await engine.cancel_run(run_id)
    return _run_out(_require_run(run_id))


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

@app.get("/api/compare")
def compare_runs(
    a: int = Query(..., description="baseline run id"),
    b: int = Query(..., description="candidate run id"),
    limit: int = Query(200, ge=1, le=5000),
):
    run_a = _require_run(a)
    run_b = _require_run(b)
    results_a = db.query(
        "SELECT r.*, c.input AS case_input, c.expected AS case_expected FROM results r "
        "LEFT JOIN cases c ON c.id=r.case_id WHERE r.run_id=? ORDER BY r.case_id",
        (a,),
    )
    results_b = db.query(
        "SELECT r.*, c.input AS case_input, c.expected AS case_expected FROM results r "
        "LEFT JOIN cases c ON c.id=r.case_id WHERE r.run_id=? ORDER BY r.case_id",
        (b,),
    )
    return metrics.build_compare(run_a, run_b, results_a, results_b, limit=limit)


# ---------------------------------------------------------------------------
# static dashboard
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="web/index.html not found")
    return FileResponse(index_path)


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
