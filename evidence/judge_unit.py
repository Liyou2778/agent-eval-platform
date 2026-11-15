import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from app import judge  # noqa: E402


def check(name, output, expected, should_pass, rules=None):
    spec = judge.normalize_rules(rules) if rules else judge.expected_spec(expected)
    verdict = judge.evaluate_rules(output, spec)
    assert verdict["passed"] == should_pass, (name, verdict)
    print("[ok] %-18s passed=%-5s reason=%s" % (name, verdict["passed"], verdict["reason"][:110]))


check("contains_all", "北京是中国的首都", "北京|中国", True)
check("contains_all-fail", "北京是首都", "北京|中国", False)
check("contains_any", "结果包含苹果", None, True, {"contains_any": ["苹果", "香蕉"]})
check("regex", "订单号 A12345 已创建", None, True, {"regex": "A\\d{5}"})
check("equals", "  42 ", "42", True)
check("equals-explicit", "42", None, True, {"equals": "42"})
check("equals-explicit-fail", "43", None, False, {"equals": "42"})
check("json_fields", '{"name": "Alice", "age": 30}', None, True, {"json_fields": {"name": "Alice"}})
check("json_fields-fail", '{"name": "Bob"}', None, False, {"json_fields": {"name": "Alice"}})
check("auto-json-object", '{"name": "Alice"}', '{"name": "Alice"}', True)
check("auto-number", "答案是 1949 年", "1949", True)
check("auto-list", "苹果 香蕉", '["苹果", "香蕉"]', True)

verdict = judge.mock_llm_judge("北京是中国的首都", "北京|中国", {"threshold": 0.6})
assert verdict["passed"] and verdict["kind"] == "llm" and verdict["mode"] == "mock", verdict
print("[ok] mock-llm           passed=%s reason=%s" % (verdict["passed"], verdict["reason"]))

verdict = judge.mock_llm_judge("北京是首都", "北京|中国", {"threshold": 0.6})
assert verdict["passed"] is False, verdict
print("[ok] mock-llm-fail      passed=%s reason=%s" % (verdict["passed"], verdict["reason"]))
print("judge rule/llm unit checks OK")
