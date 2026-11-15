"""Target executors.

Three target kinds are supported:

* ``mock``                - fully offline deterministic simulator (demo default)
* ``http``                - any HTTP JSON endpoint, with body templating and
                            response field mapping
* ``openai-compatible``   - POST ``$base_url/chat/completions``
"""

import hashlib
import json
import os
import re

import httpx

MAX_OUTPUT_CHARS = 100_000


class TargetError(Exception):
    """A target invocation failure with a coarse error category."""

    def __init__(self, category, message, retryable=True):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class TargetOutput:
    def __init__(self, output, steps=0, tokens_in=0, tokens_out=0, cost_usd=0.0, raw=None):
        self.output = output
        self.steps = int(steps or 0)
        self.tokens_in = int(tokens_in or 0)
        self.tokens_out = int(tokens_out or 0)
        self.cost_usd = float(cost_usd or 0.0)
        self.raw = raw


def _hash_int(seed):
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)


def _price(config, key, default):
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _cost(config, tokens_in, tokens_out):
    price_in = _price(config, "price_in_per_1m", 0.15)
    price_out = _price(config, "price_out_per_1m", 0.60)
    return (tokens_in / 1_000_000.0) * price_in + (tokens_out / 1_000_000.0) * price_out


def _keyword_list(expected):
    text = "" if expected is None else str(expected).strip()
    if not text:
        return []
    if "|" in text:
        return [part.strip() for part in text.split("|") if part.strip()]
    return [text]


def _mock_answer(passed, expected):
    """Build a plausible answer, correct when ``passed`` is true."""
    text = "" if expected is None else str(expected).strip()
    parsed = None
    if text:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
    is_json = isinstance(parsed, (dict, list))
    if passed:
        if is_json:
            return json.dumps(parsed, ensure_ascii=False)
        keywords = _keyword_list(expected)
        if keywords:
            return "根据已知资料，答案要点是：%s。" % "、".join(keywords)
        return "这是一个 mock 目标的确定性回答。"
    if is_json:
        return json.dumps({"error": "mock could not determine the answer"}, ensure_ascii=False)
    keywords = _keyword_list(expected)
    if len(keywords) > 1:
        kept = keywords[:-1]
        return "我的回答是：%s。剩余部分我不确定。" % "、".join(kept)
    return "抱歉，我暂时无法确定这个问题的答案。"


def _mock_plan(config, case):
    salt = str(config.get("salt", "mock-v1"))
    input_text = str(case.get("input") or "")
    expected = case.get("expected", "")
    try:
        accuracy = float(config.get("accuracy", 0.85))
    except (TypeError, ValueError):
        accuracy = 0.85
    accuracy = min(1.0, max(0.0, accuracy))
    digest = _hash_int("%s\x1f%s\x1f%s" % (salt, input_text, expected))
    passed = (digest % 1000) < int(round(accuracy * 1000))
    latency_range = config.get("latency_ms") or [20, 200]
    low = int(latency_range[0])
    high = int(latency_range[1]) if len(latency_range) > 1 else low
    span = max(1, high - low + 1)
    latency_ms = low + (digest >> 20) % span
    tokens_in = int(config.get("tokens_in_base", 18)) + max(1, len(input_text) // 2)
    answer = _mock_answer(passed, expected)
    tokens_out = int(config.get("tokens_out_base", 16)) + max(1, len(answer) // 2)
    max_steps = max(1, int(config.get("max_steps", 3)))
    steps = 1 + (digest >> 40) % max_steps
    return passed, latency_ms, tokens_in, tokens_out, steps, answer


async def _execute_mock(config, case, timeout_s, client):
    fail_mode = str(config.get("fail_mode", "wrong")).lower()
    passed, latency_ms, tokens_in, tokens_out, steps, answer = _mock_plan(config, case)
    if not passed and fail_mode == "error":
        raise TargetError("target_error", "mock target simulated an internal error", retryable=False)
    import asyncio

    await asyncio.sleep(max(0, latency_ms) / 1000.0)
    if not passed and fail_mode == "empty":
        answer = ""
    return TargetOutput(
        output=answer,
        steps=steps,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=_cost(config, tokens_in, tokens_out),
        raw={"mock_passed": passed},
    )


def _render_template(template, values):
    if isinstance(template, str):
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{%s}" % key, str(value))
        return rendered
    if isinstance(template, dict):
        return {key: _render_template(value, values) for key, value in template.items()}
    if isinstance(template, list):
        return [_render_template(item, values) for item in template]
    return template


def get_path(obj, path, default=None):
    """Fetch a nested value with dot/index notation, e.g. ``choices[0].message.content``."""
    if path is None:
        return default
    pointer = str(path).strip()
    if pointer.startswith("$"):
        pointer = pointer[1:]
    if pointer.startswith("."):
        pointer = pointer[1:]
    current = obj
    for part in re.split(r"\.|\[|\]", pointer):
        part = part.strip().strip("'\"")
        if not part:
            continue
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (TypeError, ValueError, IndexError):
                return default
        else:
            return default
    return current


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_http_output(config, data, response):
    mapping = config.get("response_map") or {}
    answer = get_path(data, mapping.get("answer")) if data is not None else None
    if answer is None and data is not None:
        answer = get_path(data, "answer")
    if answer is None:
        answer = response.text
    if not isinstance(answer, str):
        answer = json.dumps(answer, ensure_ascii=False)
    tokens_in = _as_int(get_path(data, mapping.get("tokens_in"))) if data is not None else 0
    tokens_out = _as_int(get_path(data, mapping.get("tokens_out"))) if data is not None else 0
    steps = _as_int(get_path(data, mapping.get("steps")), default=0) if data is not None else 0
    cost = _as_float(get_path(data, mapping.get("cost_usd")), default=None) if data is not None else None
    if cost is None:
        cost = _cost(config, tokens_in, tokens_out)
    return TargetOutput(
        output=answer[:MAX_OUTPUT_CHARS],
        steps=steps,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        raw={"status_code": response.status_code},
    )


async def _execute_http(config, case, timeout_s, client):
    url = config.get("url")
    if not url:
        raise TargetError("config_error", "http target requires config.url", retryable=False)
    method = str(config.get("method", "POST")).upper()
    values = {
        "input": str(case.get("input") or ""),
        "expected": str(case.get("expected") or ""),
        "case_id": str(case.get("id") or ""),
    }
    body = _render_template(config.get("body_template", {"input": "{input}"}), values)
    headers = {str(key): str(value) for key, value in (config.get("headers") or {}).items()}
    request_timeout = _as_float(config.get("timeout_s"), timeout_s) or timeout_s
    kwargs = {"timeout": request_timeout}
    if method in ("GET", "HEAD", "DELETE"):
        if isinstance(body, dict):
            kwargs["params"] = body
        elif body not in (None, ""):
            kwargs["params"] = {"input": body}
    else:
        if isinstance(body, (dict, list)):
            kwargs["json"] = body
        else:
            kwargs["content"] = str(body if body is not None else "")
            headers.setdefault("Content-Type", "text/plain")
    try:
        response = await client.request(method, str(url), headers=headers, **kwargs)
    except httpx.TimeoutException as exc:
        raise TargetError("timeout", "request timed out: %s" % exc)
    except httpx.HTTPError as exc:
        raise TargetError("connection", "request failed: %s: %s" % (type(exc).__name__, exc))
    if response.status_code >= 400:
        retryable = response.status_code >= 500 or response.status_code == 429
        raise TargetError(
            "http_error",
            "HTTP %d: %s" % (response.status_code, response.text[:300]),
            retryable=retryable,
        )
    content_type = response.headers.get("content-type", "")
    data = None
    if "json" in content_type or not content_type:
        try:
            data = response.json()
        except ValueError:
            data = None
    return _extract_http_output(config, data, response)


async def _execute_openai_compatible(config, case, timeout_s, client):
    base_url = str(config.get("base_url") or "https://api.openai.com/v1")
    model = str(config.get("model") or "gpt-4o-mini")
    api_key = str(
        config.get("api_key")
        or os.environ.get(config.get("api_key_env") or "OPENAI_API_KEY")
        or os.environ.get("EVAL_TARGET_API_KEY")
        or ""
    )
    if not api_key:
        raise TargetError(
            "config_error",
            "openai-compatible target requires config.api_key or OPENAI_API_KEY",
            retryable=False,
        )
    messages = []
    if config.get("system_prompt"):
        messages.append({"role": "system", "content": str(config["system_prompt"])})
    messages.append({"role": "user", "content": str(case.get("input") or "")})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": _as_float(config.get("temperature"), 0.0),
    }
    if config.get("max_tokens"):
        payload["max_tokens"] = _as_int(config.get("max_tokens"), 512)
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    request_timeout = _as_float(config.get("timeout_s"), timeout_s) or timeout_s
    try:
        response = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            json=payload,
            headers=headers,
            timeout=request_timeout,
        )
    except httpx.TimeoutException as exc:
        raise TargetError("timeout", "request timed out: %s" % exc)
    except httpx.HTTPError as exc:
        raise TargetError("connection", "request failed: %s: %s" % (type(exc).__name__, exc))
    if response.status_code >= 400:
        raise TargetError(
            "http_error",
            "HTTP %d: %s" % (response.status_code, response.text[:300]),
            retryable=response.status_code >= 500 or response.status_code == 429,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise TargetError("target_error", "response is not JSON: %s" % exc, retryable=False)
    mapping = config.get("response_map") or {}
    answer = get_path(data, mapping.get("answer") or "choices[0].message.content")
    if answer is None:
        raise TargetError("target_error", "cannot find answer field in response", retryable=False)
    if not isinstance(answer, str):
        answer = json.dumps(answer, ensure_ascii=False)
    usage = data.get("usage") or {}
    tokens_in = _as_int(get_path(data, mapping.get("tokens_in")) if mapping.get("tokens_in") else usage.get("prompt_tokens"))
    tokens_out = _as_int(
        get_path(data, mapping.get("tokens_out")) if mapping.get("tokens_out") else usage.get("completion_tokens")
    )
    steps = _as_int(get_path(data, mapping.get("steps")) if mapping.get("steps") else 1, default=1)
    cost = _as_float(get_path(data, mapping.get("cost_usd")), default=None) if mapping.get("cost_usd") else None
    if cost is None:
        cost = _cost(config, tokens_in, tokens_out)
    return TargetOutput(
        output=answer[:MAX_OUTPUT_CHARS],
        steps=steps,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        raw={"model": data.get("model")},
    )


async def call_target(kind, config, case, timeout_s, client):
    """Dispatch one target invocation."""
    config = config or {}
    if kind == "mock":
        return await _execute_mock(config, case, timeout_s, client)
    if kind == "http":
        return await _execute_http(config, case, timeout_s, client)
    if kind == "openai-compatible":
        return await _execute_openai_compatible(config, case, timeout_s, client)
    raise TargetError("config_error", "unsupported target kind: %s" % kind, retryable=False)
