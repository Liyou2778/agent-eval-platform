# Agent 评测中台（MVP）

像 CI 一样评测 Agent：**数据集 × 目标 × 并发跑批 × 判定 × 指标 × 两次 run 的回归 diff × Web 看板**。
默认**完全离线可跑**：内置 mock target 与 mock judge，不需要任何 API key；打开 `http://127.0.0.1:8205/` 即可用。

技术栈：Python 3.12 + FastAPI + uvicorn + SQLite（WAL）+ asyncio + httpx + 原生 JS 单页（无 npm 构建）。
只监听本机 `127.0.0.1:8205`，单进程。

---

## 1. 快速开始

```powershell
# 在 05-agent-eval-platform 目录下（本仓库根目录）
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

然后打开 **<http://127.0.0.1:8205/>**（Swagger 见 <http://127.0.0.1:8205/docs>）。

`run.ps1` 会：

1. 用 `$PSScriptRoot` 定位目录，优先使用共享 venv `..\.venv\Scripts\python.exe`（找不到再退回 PATH 里的 `python`）；
2. 检查 `fastapi/uvicorn/httpx/pydantic`，缺失时按 `requirements.txt` 安装；
3. 若 `data/eval.db` 不存在，自动执行 `seed.py --direct` 写入 200 条中文用例 + 5 个 target（首次启动即可看到数据）；
4. 以 `uvicorn app.main:app --host 127.0.0.1 --port 8205` 启动。

手动 seed 的三种方式：

```powershell
python seed.py                                 # 只生成 data/seed_cases.json / .jsonl / seed_targets.json
python seed.py --direct [--reset]              # 直接写 SQLite（--reset 先清库）
python seed.py --api http://127.0.0.1:8205     # 通过 REST API 导入（本文实测用的方式）
```

演示数据是确定性的：200 条中文问答（唯一问题 200 条，域分布 地理 30 / 科学 40 / 编程 35 / 历史 25 / 生活 20 / 数学 50），
mock target 按 `sha256(salt + input + expected)` 确定性地决定通过/失败、延迟（20–200ms）、步数与 tokens，
因此除墙钟时间外，同一份数据的通过率、P50/P95、tokens、diff 统计每次一致。

---

## 2. 架构

```
┌────────────────────────── Browser（单页看板，无构建） ──────────────────────────┐
│  ① 数据集：列表 / 新建 / JSON 或 JSONL 导入 / 用例预览                          │
│  ② 目标：列表 / 新建（mock|http|openai-compatible）/ 探活                      │
│  ③ 运行：发起跑批 / 进度轮询 / 指标卡片 / 延迟柱状图 / 可展开失败用例表        │
│  ④ 对比：选 A/B run → 指标差值 + 修复/回归/仍失败/仍通过 + 用例级 diff         │
└──────────────────────────────────────┬─────────────────────────────────────────┘
                                       │ fetch JSON（同源 /api/*）
┌──────────────────────────────────────▼─────────────────────────────────────────┐
│ FastAPI app  app/main.py                                                        │
│   /api/health  /api/stats  /api/echo（离线 http target 靶子）                   │
│   /api/datasets*  /api/targets*  /api/runs*  /api/compare                       │
│   pydantic 请求模型 + 行序列化 + 静态看板（/、/static/*）                        │
├─────────────────────┬──────────────────────────────┬───────────────────────────┤
│ engine.py           │ judge.py                     │ targets.py                │
│ asyncio.Semaphore   │ rule: contains_all/any、     │ mock（离线确定性）        │
│ 每用例 timeout      │  regex、equals、json_fields  │ http（body 模板+字段映射）│
│ 指数退避重试        │ llm: mock 启发式 /           │ openai-compatible         │
│ cancel 传播         │  openai 兼容 Chat Completions│ httpx.AsyncClient         │
├─────────────────────┴──────────────┬───────────────┴───────────────────────────┤
│ metrics.py：指标聚合 + 回归 diff   │ db.py：SQLite（WAL）连接与 schema         │
├────────────────────────────────────┴───────────────────────────────────────────┤
│ SQLite data/eval.db：datasets / cases / targets / runs / results                │
└────────────────────────────────────────────────────────────────────────────────┘
```

关键实现：

- **并发**：每个用例一个 `asyncio.Task`，由 `asyncio.Semaphore(concurrency)` 限流；`httpx.AsyncClient` 连接池大小跟随并发。
- **超时**：`asyncio.wait_for(target_call, timeout_s)`，httpx 自身也带超时；超时统一归类为 `error_type=timeout`。
- **重试**：仅对可重试错误（timeout / connection / HTTP 5xx / 429）做指数退避（0.2s、0.4s、0.8s…，上限 2s），重试次数用完后记录 error。
- **取消**：`POST /api/runs/{id}/cancel` 先置取消事件再 `task.cancel()`，并**等待该 run 的所有在途任务结束**后才返回；返回后不会再写入新 results，run 状态为 `cancelled`，已产生的部分结果仍保留并聚合指标。
- **进度**：`GET /api/runs/{id}` 返回 `progress.done/total/percent`（done = 已写 results 数），前端每 700ms 轮询。
- **判分**：先跑目标再用 judge 判定，`results.status ∈ {pass, fail, error}`；错误（超时、HTTP 错误、judge 自身失败）单独计数，不混入 fail。

---

## 3. 数据模型（SQLite）

| 表 | 主要字段 |
| --- | --- |
| `datasets` | `id, name, version, created_at` |
| `cases` | `id, dataset_id, input, expected, tags, meta, active, created_at` |
| `targets` | `id, name, kind, config(JSON), created_at` |
| `runs` | `id, dataset_id, target_id, status, config(JSON), metrics(JSON), total, error, started_at, ended_at, created_at` |
| `results` | `id, run_id, case_id, status, latency_ms, steps, tokens_in, tokens_out, cost_usd, attempts, output, error, error_type, judge(JSON), created_at` |

`runs.status`：`queued → running → completed / cancelled / failed`；启动时会把上次进程异常退出遗留的 `queued/running` 标记为 `failed`（error=`interrupted by server restart`）。

---

## 4. API

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（status/version/db 路径） |
| GET | `/api/stats` | 数据集/用例/目标/run/result 数量与 run 状态分布 |
| GET | `/api/datasets` | 数据集列表（含 case_count） |
| POST | `/api/datasets` | `{name, version}` 新建 |
| POST | `/api/datasets/{id}/cases` | 批量导入用例：`{text, format: auto\|json\|jsonl}` 或 `{cases: [...]}` |
| GET | `/api/datasets/{id}/cases` | 用例列表，`limit/offset/active` |
| GET | `/api/targets` | 目标列表 |
| POST | `/api/targets` | `{name, kind, config}`，kind = `mock\|http\|openai-compatible` |
| POST | `/api/targets/{id}/probe` | 单条探活，body `{input}`，返回 output/latency/tokens 或 error_type |
| GET | `/api/runs` | run 列表（含进度/指标），`limit/status` |
| POST | `/api/runs` | 发起跑批，见下方 body |
| GET | `/api/runs/{id}` | run 详情（status/progress/config/metrics） |
| GET | `/api/runs/{id}/results` | 结果列表，`status=failed`（=fail+error）、`pass`、`error`，`limit/offset`；JOIN 出 case 的 input/expected |
| POST | `/api/runs/{id}/cancel` | 取消 run（幂等；已结束则原样返回） |
| GET | `/api/compare?a=<runA>&b=<runB>` | 指标差值 + 修复/回归/仍失败/仍通过/新增/移除 用例列表 |
| POST | `/api/echo` | 离线 http target 靶子（回显 `prompt`，返回 answer/steps/usage/cost） |

发起 run 的 body：

```json
{
  "dataset_id": 1,
  "target_id": 1,
  "concurrency": 8,
  "retries": 1,
  "timeout_s": 30,
  "judge": {"kind": "rule", "mode": "mock"}
}
```

`judge` 可扩展字段：`threshold`（llm mock 阈值，默认 0.6）、`rules`（显式规则，覆盖 expected 推导）、`model/base_url/api_key_env`（llm openai 模式）。

### target 配置示例

```jsonc
// mock：离线确定性模拟，约 accuracy 比例通过；按 case 哈希产生延迟/步数/tokens
{"accuracy": 0.85, "salt": "demo-v1", "latency_ms": [20, 200], "max_steps": 3,
 "price_in_per_1m": 0.15, "price_out_per_1m": 0.60}

// http：任意 JSON 接口；body_template 里可插值 {input}/{expected}/{case_id}，响应按点号路径映射
{"url": "http://127.0.0.1:8205/api/echo", "method": "POST",
 "headers": {"Content-Type": "application/json"},
 "body_template": {"prompt": "{input}"},
 "timeout_s": 10,
 "response_map": {"answer": "answer", "steps": "steps",
                  "tokens_in": "usage.input_tokens", "tokens_out": "usage.output_tokens",
                  "cost_usd": "cost_usd"}}

// openai-compatible：POST {base_url}/chat/completions（离线需自备 key，默认不使用）
{"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
 "api_key_env": "OPENAI_API_KEY", "temperature": 0, "max_tokens": 256, "timeout_s": 30}
```

### 判定规则

`expected` 支持两种写法：

1. **关键词串**：`"曼谷|泰国"` 等价于 `contains_all: ["曼谷","泰国"]`；无 `|` 时等价于单个 `contains_all`。
   纯数字/年份（如 `"1949"`）也按关键词包含判定（不会被当成 JSON 数字做 equals）。
2. **JSON 规则对象**：如
   `{"contains_all":["A","B"],"contains_any":["C"],"regex":"\\d{5}","equals":"42","json_fields":{"name":"Alice"}}`，
   多个约束取 AND；JSON 数组被视作 `contains_all`，普通 JSON 对象被视作 `json_fields` 子集匹配。

| 断言 | 语义 |
| --- | --- |
| `contains_all` | 输出包含全部关键词（大小写不敏感） |
| `contains_any` | 输出包含任一关键词 |
| `regex` | 正则搜索（默认 `re.IGNORECASE`，可配 `regex_flags`） |
| `equals` | 归一化空白/大小写后完全相等 |
| `json_fields` | 输出按 JSON 解析后，按点号路径逐字段等于期望值 |

LLM-as-judge：`kind=llm, mode=mock` 用关键词覆盖率启发式（默认阈值 0.6）离线判定；
`mode=openai` 调用 `{base_url}/chat/completions` 要求模型返回 `{"pass": bool, "reason": "..."}`。
判定结果写入 `results.judge`：`{kind, mode, passed, reason, score?, checks?}`，失败用例表可直接展开查看 `judge.reason`。

---

## 5. 指标口径

| 指标 | 口径 |
| --- | --- |
| 通过率 `pass_rate` | `passed / done`，`done` = 已产生 results 的用例数（正常结束 = total；取消的 run 用已完成数）；`error` 计入分母且视为未通过 |
| pass@1 | 首轮（`attempts == 1`）即通过的用例数 / done；重试后才通过的不算。当前每个用例只采样 1 次，因此正常 run 中 pass@1 = 通过率，重试命中时两者产生差异 |
| 平均/P50/P95 延迟 | 单用例**目标调用**墙钟耗时（含重试与退避等待，不含 judge 判定）；P50/P95 用 nearest-rank 百分位 |
| 平均步数 | `steps` 均值，仅统计判定为 pass/fail 的用例 |
| tokens | 每用例最终一次尝试记录（失败记录为 0）的 `tokens_in/tokens_out` 合计 |
| 成本 | 按 target 配置的 `price_in_per_1m / price_out_per_1m` 计算并累加，单位 USD |
| 错误分类 | `status=error` 按 `error_type` 计数：`timeout / connection / http_error / config_error / target_error / judge_error` |
| 重试用例 | `attempts > 1` 的用例数 |
| 回归 diff | 按 `case_id` 对齐两次 run：修复 = A 非 pass → B pass；回归 = A pass → B 非 pass；仍失败 = 两次都非 pass；仍通过 = 两次都 pass；另有仅 B 存在的新增用例、仅 A 存在的移除用例 |

---

## 6. 实测数字

环境：Windows 11 家庭版 10.0.26300、Python 3.12.2、fastapi 0.162.5、uvicorn 0.54.0、httpx 0.28.1、pydantic 2.14.3、Node v24.19.0（仅用于 JS 语法检查）。
时间：2026-11-15（本机）。用例集：`中文问答基准 v1`，200 条；`run #1` 目标为 `mock-baseline-85`，`run #2` 目标为 `mock-improved-90`，均为 `concurrency=8, retries=1, timeout_s=30, judge=rule`。

| 指标 | run #1 baseline | run #2 improved |
| --- | --- | --- |
| 用例数 / 完成 | 200 / 200 | 200 / 200 |
| 墙钟时间（客户端 POST → completed，含 100ms 轮询间隔） | **4.08 s** | **3.33 s** |
| 服务端耗时（started_at → ended_at） | 3.44 s | 2.88 s |
| 通过 / 失败 / 错误 | 174 / 26 / 0 | 179 / 21 / 0 |
| 通过率 / pass@1 | **87.0% / 87.0%** | **89.5% / 89.5%** |
| 平均延迟 / P50 / P95 | **115.6 / 109.9 / 201.2 ms** | **98.5 / 93.7 / 170.9 ms** |
| 平均步数 | 1.94 | 1.92 |
| tokens in / out / 合计 | 5152 / 4958 / **10110** | 5152 / 4953 / 10105 |
| 成本 | $0.003748 | $0.003745 |
| 重试用例 | 0 | 0 |

`GET /api/compare?a=1&b=2` 实测 diff：

```json
{"counts": {"fixed": 21, "regressed": 16, "still_failed": 5, "still_passed": 158,
            "new_case": 0, "removed_case": 0},
 "pass_rate_delta": {"a": 0.87, "b": 0.895, "delta": 0.025},
 "p95_delta": {"a": 201.2, "b": 170.9, "delta": -30.3}}
```

其它实测：

- **取消**（run #3，`concurrency=2`）：发起后 0.27 s 调用 cancel 返回，状态 `cancelled`，results 冻结在 `13/200`（+1s/+2s/+3s 均为 13，不再增长）。
- **超时 + 重试**（run #4，3 条用例，`timeout_s=0.5, retries=1`，目标 `mock-slow-timeout` 延迟 2000–2500ms）：`error_counts={"timeout": 3}`，3/3 `error_type=timeout`，`attempts=2`，单用例延迟约 **1146 ms**（2 × 500ms + 200ms 退避）。
- **http target 全流程**（run #7，目标 `http-demo` → `/api/echo`，body 模板 + 响应字段映射）：3/3 通过，`avg_steps=3.0`，tokens 72/90，成本 $0.000162，平均延迟 7.8 ms。
- **LLM-as-judge（mock 离线）**（run #6，200 条）：通过 179/200（89.5%），失败用例含 `judge.reason`，例如
  `mock judge: 1/2 expected keywords matched (score 0.50, threshold 0.60); missing: 泰国`。
- **探活**：mock 目标 `ok=true`；http 目标 `ok=true, output="echo: ping", steps=3, tokens_in=4, tokens_out=10`；未配 key 的 openai-compatible 目标返回 `ok=false, error_type=config_error`（离线安全失败）。
- **前端**：`GET /` 200（8387 字节），`/static/app.js` 34523 字节、`/static/style.css` 11402 字节；HTML 中 `data-tab="datasets|targets|runs|compare"` 四个 tab 均存在；`node --check web/app.js` 通过。

> 说明：mock target 的通过/判定/tokens 由哈希决定，可复现；墙钟时间与延迟受机器负载影响，重跑会有小幅波动。
> 本次实测的完整终端输出保存在 `data/_verify/evidence.txt` 与 `data/_verify/curl_evidence.txt`（临时目录，可删除）。

---

## 7. 自验清单（a–f）

| 项 | 命令 / 操作 | 结果 |
| --- | --- | --- |
| a | `python seed.py --api http://127.0.0.1:8205` | `dataset id=1: imported=200 skipped=0 total_cases=200`；5 个 target 创建成功 |
| b | `POST /api/runs {dataset_id:1, target_id:1, concurrency:8, retries:1, timeout_s:30}` | completed 200/200，wall **4.08 s**，通过率 **87.0%**，P50 **109.9 ms**，P95 **201.2 ms**，tokens **10110** |
| c | 同样 200 条用 target 2 再跑一次，`GET /api/compare?a=1&b=2` | 修复 21 / 回归 16 / 仍失败 5 / 仍通过 158；通过率 +2.5pp；P95 −30.3ms |
| d | 起 run 后立即 `POST /api/runs/3/cancel` | HTTP 200，`status=cancelled`，results 冻结 13 条，3 秒内不再增长 |
| e | 对 `mock-slow-timeout` 起 run，`timeout_s=0.5, retries=1` | 3/3 `error_type=timeout`，`attempts=2`，`error_counts={"timeout":3}` |
| f | `GET /` + 抓 HTML；`node --check web/app.js` | 4 个 tab 均渲染，静态资源 200，JS 语法检查通过 |

复现（服务已启动时）：

```powershell
python seed.py --api http://127.0.0.1:8205
curl.exe -s -X POST http://127.0.0.1:8205/api/runs -H "Content-Type: application/json" `
  --data-binary '@data/_verify/run_payload.json'   # 或自建等价 JSON body
curl.exe -s "http://127.0.0.1:8205/api/compare?a=1&b=2"
```

---

## 8. 目录结构

```
05-agent-eval-platform/
├─ app/
│  ├─ __init__.py       版本号
│  ├─ main.py           FastAPI 路由、序列化、静态看板挂载
│  ├─ db.py             SQLite schema / WAL / 连接与查询辅助
│  ├─ models.py         pydantic 请求模型
│  ├─ engine.py         asyncio 跑批引擎（Semaphore/超时/重试/取消/进度）
│  ├─ targets.py        mock / http / openai-compatible 目标执行器
│  ├─ judge.py          rule 断言 + LLM-as-judge
│  └─ metrics.py        指标聚合 + /api/compare 回归 diff
├─ web/
│  ├─ index.html        单页 4 tab
│  ├─ app.js            原生 JS：轮询、指标卡片、失败用例展开、条形图、对比视图
│  └─ style.css         深色主题
├─ seed.py              200 条中文问答 + 5 个 target（确定性）
├─ run.ps1              一键启动（$PSScriptRoot、venv 探测、首次自动 seed）
├─ requirements.txt     fastapi/uvicorn/httpx/pydantic
├─ LICENSE              MIT
└─ data/                运行时生成（.gitignore）：eval.db、seed_cases.json/.jsonl、seed_targets.json
```

代码约束：标识符全英文、无写死绝对路径（应用侧统一 `Path(__file__).resolve().parent` 推导；`run.ps1` 用 `$PSScriptRoot`），
默认离线、只监听本机 8205，未启动任何子服务。

---

## 9. 已知限制

- **mock target 是演示替身**：它会拿到用例 `expected` 并按哈希生成“约 85% 正确”的回答（模拟一个模型），仅用于离线演示/联调，不能代表真实模型能力；真实评测请配置 http / openai-compatible 目标。
- **单进程单库**：SQLite 单连接 + uvicorn 单 worker；没有分布式调度、任务队列、断点续跑（进程崩溃时在途 run 会在下次启动标记为 failed）。
- **无鉴权**：仅绑定 127.0.0.1，适合本机 MVP；API key 不落库（openai 目标/judge 从环境变量或请求配置读取）。
- **openai-compatible 目标与 openai judge 未做联网实测**（本机无 key，默认离线），代码路径已实现、失败会以 `http_error/connection/config_error` 归类。
- **判定较简单**：mock judge 只看关键词覆盖率；规则断言不支持嵌套逻辑（AND/OR 组合需自己写成多条规则）；无人工复核、标注、统计显著性分析。
- **对比假设**：diff 按 `case_id` 对齐，适合同一数据集；跨数据集对比只会把不同 case 归入新增/移除。
- **前端能力**：用例预览最多 200 条、失败用例表最多 100 条（API 支持 limit/offset，前端暂未做分页控件）；轮询间隔 700ms，无 WebSocket。
- **数字匹配**：关键词包含是子串匹配（大小写不敏感），不做中文分词/语义归一，可能出现“关键词出现在无关上下文也通过”的情况。
- **seed 设计值 85%**：实测 run #1 为 87.0%（哈希取模的实际分布），不是精确 85%。

---

## 10. License

MIT，见 [LICENSE](LICENSE)。
