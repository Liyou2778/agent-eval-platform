"""Judging: deterministic rule assertions plus LLM-as-judge.

Rule specs are parsed from the case ``expected`` field, or supplied
explicitly as ``judge.rules`` in the run configuration.  Supported rule
keys: ``contains_all``, ``contains_any``, ``contains``, ``not_contains``,
``regex``, ``equals``, ``json_fields``.
"""

import json
import os
import re

RULE_KEYS = (
    "contains_all",
    "contains_any",
    "contains",
    "not_contains",
    "regex",
    "equals",
    "json_fields",
)


class JudgeError(Exception):
    """Raised when the judge itself cannot produce a verdict."""

    def __init__(self, message):
        super().__init__(message)
        self.category = "judge_error"


def _text(value):
    return "" if value is None else str(value)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [_text(item) for item in value]
    return [_text(value)]


def _match_keyword(output_lower, keyword):
    return _text(keyword).strip().lower() in output_lower


def normalize_rules(raw):
    """Validate an explicit rule spec."""
    if not isinstance(raw, dict) or not raw:
        raise ValueError("rules must be a non-empty object")
    spec = {}
    for key, value in raw.items():
        if key not in RULE_KEYS:
            raise ValueError("unknown rule key: %s" % key)
        spec[key] = value
    return spec


def expected_spec(expected):
    """Derive a rule spec from the case ``expected`` value.

    * JSON object with rule keys -> rule spec
    * any other JSON object      -> ``json_fields`` subset match
    * JSON array                 -> ``contains_all``
    * plain text with ``|``      -> ``contains_all`` of the parts
    * plain text                 -> single ``contains_all`` keyword
    * empty                      -> no constraint (always passes)
    """
    text = _text(expected).strip()
    if not text:
        return {}
    parsed = None
    if text[:1] in ("{", "[", '"'):
        # Only structured JSON is parsed. Bare scalars such as "1949" stay
        # plain keywords so an expected year is a contains_all check rather
        # than an exact-equality check.
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
    if isinstance(parsed, dict) and parsed:
        if any(key in RULE_KEYS for key in parsed):
            return normalize_rules(parsed)
        return {"json_fields": parsed}
    if isinstance(parsed, list):
        return {"contains_all": [_text(item) for item in parsed]}
    if isinstance(parsed, str):
        text = parsed.strip()
        if not text:
            return {}
    if "|" in text:
        parts = [part.strip() for part in text.split("|") if part.strip()]
        return {"contains_all": parts} if parts else {}
    return {"contains_all": [text]}


def _get_path(obj, path):
    current = obj
    for part in str(path).split("."):
        if part == "":
            continue
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (TypeError, ValueError, IndexError):
                return None
        else:
            return None
    return current


def evaluate_rules(output, spec):
    """Return a judge verdict dict with per-check details."""
    if not spec:
        return {
            "kind": "rule",
            "mode": "rule",
            "passed": True,
            "reason": "no rule constraints (empty expected), treated as pass",
            "checks": [],
        }
    text = output if isinstance(output, str) else _text(output)
    lower = text.lower()
    checks = []

    def add(kind, ok, detail):
        checks.append({"type": kind, "ok": bool(ok), "detail": detail})

    if "contains_all" in spec:
        keywords = _as_list(spec["contains_all"])
        missing = [kw for kw in keywords if not _match_keyword(lower, kw)]
        add(
            "contains_all",
            not missing,
            "matched %d/%d keywords%s"
            % (
                len(keywords) - len(missing),
                len(keywords),
                "" if not missing else "; missing: " + ", ".join(missing),
            ),
        )
    if "contains_any" in spec:
        keywords = _as_list(spec["contains_any"])
        hits = [kw for kw in keywords if _match_keyword(lower, kw)]
        add(
            "contains_any",
            bool(hits),
            "matched %d/%d keywords%s"
            % (len(hits), len(keywords), "" if hits else "; none matched"),
        )
    if "contains" in spec:
        keyword = _text(spec["contains"])
        ok = _match_keyword(lower, keyword)
        add("contains", ok, "keyword %r %s" % (keyword, "found" if ok else "not found"))
    if "not_contains" in spec:
        keywords = _as_list(spec["not_contains"])
        hits = [kw for kw in keywords if _match_keyword(lower, kw)]
        add("not_contains", not hits, "forbidden keywords hit: %s" % (", ".join(hits) or "none"))
    if "regex" in spec:
        pattern = _text(spec["regex"])
        flags = 0
        flag_text = _text(spec.get("regex_flags", "i")).lower()
        if "i" in flag_text:
            flags |= re.IGNORECASE
        if "m" in flag_text:
            flags |= re.MULTILINE
        if "s" in flag_text:
            flags |= re.DOTALL
        try:
            matched = re.search(pattern, text, flags) is not None
            add("regex", matched, "pattern %r %s" % (pattern, "matched" if matched else "did not match"))
        except re.error as exc:
            add("regex", False, "invalid pattern: %s" % exc)
    if "equals" in spec:
        expected_text = " ".join(_text(spec["equals"]).split()).lower()
        actual_text = " ".join(text.split()).lower()
        add("equals", actual_text == expected_text, "actual=%r expected=%r" % (actual_text, expected_text))
    if "json_fields" in spec:
        fields = spec["json_fields"]
        if not isinstance(fields, dict):
            add("json_fields", False, "json_fields must be an object")
        else:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                parsed = None
            if parsed is None:
                add("json_fields", False, "output is not valid JSON")
            else:
                problems = []
                for path, expected_value in fields.items():
                    actual_value = _get_path(parsed, path)
                    if actual_value != expected_value:
                        problems.append("%s=%r (expected %r)" % (path, actual_value, expected_value))
                add("json_fields", not problems, "; ".join(problems) or "all fields matched")

    passed = all(check["ok"] for check in checks)
    if passed:
        reason = "all %d rule checks passed" % len(checks)
    else:
        failures = [check["detail"] for check in checks if not check["ok"]]
        reason = "rule check failed: " + " | ".join(failures)
    return {
        "kind": "rule",
        "mode": "rule",
        "passed": passed,
        "reason": reason,
        "checks": checks,
    }


def _keywords_from_spec(spec):
    keywords = []
    for key in ("contains_all", "contains_any"):
        for keyword in _as_list(spec.get(key)):
            if keyword and keyword not in keywords:
                keywords.append(keyword)
    return keywords


def mock_llm_judge(output, expected, config):
    """Deterministic offline heuristic judge (keyword coverage ratio)."""
    spec = expected_spec(expected)
    keywords = _keywords_from_spec(spec)
    threshold = float(config.get("threshold", 0.6))
    text = output if isinstance(output, str) else _text(output)
    lower = text.lower()
    if keywords:
        matched = [kw for kw in keywords if _match_keyword(lower, kw)]
        score = len(matched) / len(keywords)
        passed = score >= threshold
        reason = "mock judge: %d/%d expected keywords matched (score %.2f, threshold %.2f)" % (
            len(matched),
            len(keywords),
            score,
            threshold,
        )
        missing = [kw for kw in keywords if kw not in matched]
        if missing:
            reason += "; missing: " + ", ".join(missing)
    else:
        score = 1.0 if text.strip() else 0.0
        passed = bool(text.strip())
        reason = "mock judge: no expected keywords, pass on non-empty output"
    return {
        "kind": "llm",
        "mode": "mock",
        "passed": passed,
        "score": round(score, 4),
        "threshold": threshold,
        "reason": reason,
        "keywords": keywords,
    }


def _parse_judge_json(content):
    text = _text(content).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise JudgeError("judge response is not JSON: %s" % text[:300])
        try:
            return json.loads(match.group(0))
        except (TypeError, ValueError) as exc:
            raise JudgeError("judge response is not JSON: %s" % exc)


async def _openai_judge(output, expected, config, case, client):
    base_url = _text(
        config.get("base_url") or os.environ.get("EVAL_JUDGE_BASE_URL") or "https://api.openai.com/v1"
    )
    model = _text(config.get("model") or os.environ.get("EVAL_JUDGE_MODEL") or "gpt-4o-mini")
    api_key = _text(
        config.get("api_key")
        or os.environ.get(config.get("api_key_env") or "EVAL_JUDGE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    if not api_key:
        raise JudgeError("openai judge requires an api key (judge.api_key or OPENAI_API_KEY)")
    timeout_s = float(config.get("timeout_s", 30))
    prompt = (
        "You are a strict evaluator for an AI agent benchmark.\n"
        "Decide whether the candidate answer satisfies the expected answer.\n"
        'Reply with JSON only, for example {"pass": true, "reason": "..."}.\n\n'
        "[question]\n%s\n\n[expected]\n%s\n\n[candidate]\n%s\n"
        % (_text(case.get("input")), _text(case.get("expected")), _text(output))
    )
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    if config.get("max_tokens"):
        payload["max_tokens"] = int(config["max_tokens"])
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    try:
        response = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout_s,
        )
    except Exception as exc:  # network layer
        raise JudgeError("judge request failed: %s: %s" % (type(exc).__name__, exc))
    if response.status_code >= 400:
        raise JudgeError("judge request failed: HTTP %d: %s" % (response.status_code, response.text[:300]))
    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise JudgeError("unexpected judge response: %s" % exc)
    verdict = _parse_judge_json(content)
    passed = bool(verdict.get("pass", verdict.get("passed", False)))
    return {
        "kind": "llm",
        "mode": "openai",
        "passed": passed,
        "reason": _text(verdict.get("reason")),
        "model": model,
        "raw": content[:2000],
    }


async def judge_case(judge_config, case, output, client):
    """Run the configured judge and return its verdict dict.

    ``judge_config`` is arbitrary JSON from the run request:
    ``{"kind": "rule"|"llm", "mode": "mock"|"openai", ...}``.
    """
    config = judge_config or {}
    kind = _text(config.get("kind") or "rule").lower()
    mode = _text(config.get("mode") or "mock").lower()
    if kind == "rule":
        if config.get("rules") is not None:
            try:
                spec = normalize_rules(config["rules"])
            except ValueError as exc:
                raise JudgeError("invalid judge rules: %s" % exc)
        else:
            spec = expected_spec(case.get("expected"))
        return evaluate_rules(output, spec)
    if kind == "llm":
        if mode == "mock":
            return mock_llm_judge(output, case.get("expected"), config)
        if mode == "openai":
            return await _openai_judge(output, case.get("expected"), config, case, client)
        raise JudgeError("unsupported judge mode: %s" % mode)
    raise JudgeError("unsupported judge kind: %s" % kind)
