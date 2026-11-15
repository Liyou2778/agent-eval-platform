"""Demo seed generator for the Agent Evaluation Platform.

Usage:
    python seed.py                        # write data/seed_cases.json / .jsonl / seed_targets.json
    python seed.py --direct [--reset]     # write the demo data straight into SQLite
    python seed.py --api http://127.0.0.1:8205   # import through the REST API

The 200 Chinese Q&A cases and the mock targets are deterministic: the same
seed always yields the same questions, the same mock pass/fail decisions,
latencies and token counts.
"""

import argparse
import json
import random
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SEED_VERSION = "seed-v1"

CAPITALS = [
    ("中国", "北京"),
    ("日本", "东京"),
    ("法国", "巴黎"),
    ("德国", "柏林"),
    ("英国", "伦敦"),
    ("美国", "华盛顿"),
    ("加拿大", "渥太华"),
    ("澳大利亚", "堪培拉"),
    ("巴西", "巴西利亚"),
    ("印度", "新德里"),
    ("俄罗斯", "莫斯科"),
    ("意大利", "罗马"),
    ("西班牙", "马德里"),
    ("韩国", "首尔"),
    ("埃及", "开罗"),
    ("南非", "比勒陀利亚"),
    ("泰国", "曼谷"),
    ("越南", "河内"),
    ("墨西哥", "墨西哥城"),
    ("阿根廷", "布宜诺斯艾利斯"),
    ("葡萄牙", "里斯本"),
    ("荷兰", "阿姆斯特丹"),
    ("瑞士", "伯尔尼"),
    ("瑞典", "斯德哥尔摩"),
    ("挪威", "奥斯陆"),
    ("希腊", "雅典"),
    ("土耳其", "安卡拉"),
    ("新西兰", "惠灵顿"),
    ("印度尼西亚", "雅加达"),
    ("沙特阿拉伯", "利雅得"),
]

SCIENCE = [
    ("水的化学式是什么？", "H2O|氢"),
    ("地球唯一的天然卫星是什么？", "月球"),
    ("光在真空中的速度大约是多少？", "30万|秒"),
    ("人体面积最大的器官是什么？", "皮肤"),
    ("空气中含量最多的气体是什么？", "氮气|78%"),
    ("声音在空气中的传播速度大约是多少？", "340"),
    ("地球绕太阳公转一圈大约需要多少天？", "365"),
    ("标准大气压下水的沸点是多少摄氏度？", "100"),
    ("植物进行光合作用主要吸收哪种气体？", "二氧化碳"),
    ("血液中负责运输氧气的是什么细胞？", "红细胞"),
    ("电子带何种电荷？", "负电"),
    ("地球表面的重力加速度大约是多少？", "9.8"),
    ("地球的平均半径大约是多少千米？", "6371"),
    ("太阳系中体积最大的行星是哪一颗？", "木星"),
    ("成年人的正常体温大约是多少摄氏度？", "36.5"),
    ("DNA 双螺旋结构是哪两位科学家提出的？", "沃森|克里克"),
    ("元素周期表中原子序数为 1 的元素是什么？", "氢"),
    ("常温下唯一呈液态的金属是什么？", "汞"),
    ("植物细胞中进行光合作用的场所是什么？", "叶绿体"),
    ("声音不能在哪种介质中传播？", "真空"),
    ("月球表面的重力大约是地球的多少？", "六分之一"),
    ("彩虹的七种颜色中排在最前面的是什么颜色？", "红"),
    ("彩虹的七种颜色中排在最后面的是什么颜色？", "紫"),
    ("地球自转一圈大约需要多少小时？", "24"),
    ("酸雨的主要污染物是什么？", "二氧化硫"),
    ("人类体细胞中有多少对染色体？", "23"),
    ("标准大气压大约是多少帕斯卡？", "101325"),
    ("水的密度大约是多少克每立方厘米？", "1"),
    ("二氧化碳通入澄清石灰水会有什么现象？", "浑浊"),
    ("铁生锈需要同时接触哪两种物质？", "氧气|水"),
    ("声音的音调高低由什么物理量决定？", "频率"),
    ("电磁波在真空中的传播速度等于什么的速度？", "光速"),
    ("人体呼吸系统中进行气体交换的主要器官是什么？", "肺"),
    ("太阳内部释放能量主要依靠哪种核反应？", "核聚变"),
    ("世界上第一位进入太空的宇航员是谁？", "加加林"),
    ("世界上第一位踏上月球的人是谁？", "阿姆斯特朗"),
    ("地球表面海洋大约占多少比例？", "71%"),
    ("密度最小的气体是什么？", "氢气"),
    ("水在标准大气压下多少摄氏度结冰？", "0"),
    ("成年人全身共有多少块骨骼？", "206"),
]

PROGRAMMING = [
    ("Python 中定义函数使用哪个关键字？", "def"),
    ("Python 中向控制台输出内容使用哪个函数？", "print"),
    ("Python 中向列表末尾追加元素使用哪个方法？", "append"),
    ("Python 字典中通过什么来取值？", "键"),
    ("Python 中 None 表示什么含义？", "空值"),
    ("SQL 中查询数据使用哪个关键字？", "SELECT"),
    ("SQL 中插入数据使用哪个关键字？", "INSERT"),
    ("SQL 中更新数据使用哪个关键字？", "UPDATE"),
    ("HTTP 状态码 404 表示什么？", "未找到"),
    ("HTTP 状态码 500 表示什么？", "服务器错误"),
    ("HTTP 状态码 200 表示什么？", "成功"),
    ("Git 中查看工作区状态的命令是什么？", "git status"),
    ("Git 中提交改动的命令是什么？", "git commit"),
    ("Git 中拉取远程更新的命令是什么？", "git pull"),
    ("Git 中推送本地提交的命令是什么？", "git push"),
    ("JavaScript 中声明常量使用哪个关键字？", "const"),
    ("JavaScript 中声明可重新赋值的变量可以使用哪些关键字？", "let|var"),
    ("HTML 中超链接使用哪个标签？", "a"),
    ("CSS 中设置文字颜色的属性是什么？", "color"),
    ("JSON 对象中键和值之间用什么符号分隔？", "冒号"),
    ("正则表达式中匹配一个数字的元字符是什么？", "\\d"),
    ("Linux 中列出目录内容的命令是什么？", "ls"),
    ("Linux 中切换工作目录的命令是什么？", "cd"),
    ("Linux 中查看文件内容的常用命令是什么？", "cat"),
    ("Python 内置的虚拟环境模块是什么？", "venv"),
    ("Python 安装第三方包常用的工具是什么？", "pip"),
    ("Python 中打开文件的常用内置函数是什么？", "open"),
    ("Python 中 try/except 语句用于什么？", "异常处理"),
    ("后进先出（LIFO）的数据结构叫什么？", "栈"),
    ("先进先出（FIFO）的数据结构叫什么？", "队列"),
    ("二分查找的时间复杂度是多少？", "O(log n)|对数"),
    ("冒泡排序的平均时间复杂度是多少？", "O(n^2)|平方"),
    ("REST 风格接口中创建资源通常使用哪个 HTTP 方法？", "POST"),
    ("REST 风格接口中删除资源通常使用哪个 HTTP 方法？", "DELETE"),
    ("关系数据库中用于唯一标识一条记录的字段叫什么？", "主键"),
]

HISTORY = [
    ("中华人民共和国成立于哪一年？", "1949"),
    ("辛亥革命发生在哪一年？", "1911"),
    ("五四运动发生在哪一年？", "1919"),
    ("中国共产党成立于哪一年？", "1921"),
    ("中国人民抗日战争胜利于哪一年？", "1945"),
    ("中国改革开放开始于哪一年？", "1978"),
    ("秦始皇统一中国是在哪一年？", "221"),
    ("唐朝建立于哪一年？", "618"),
    ("明朝建立于哪一年？", "1368"),
    ("清朝建立于哪一年？", "1636"),
    ("中国古代四大发明包括哪些？", "造纸术|指南针|火药|印刷术"),
    ("中国最长的河流是哪一条？", "长江"),
    ("中国面积最大的沙漠是哪一个？", "塔克拉玛干"),
    ("世界最高峰是哪一座？", "珠穆朗玛峰"),
    ("中国最大的淡水湖是哪一个？", "鄱阳湖"),
    ("中国人口最多的省份是哪一个？", "广东"),
    ("中国面积最大的省级行政区是哪一个？", "新疆"),
    ("京剧形成于哪个朝代？", "清朝"),
    ("中秋节的传统食物是什么？", "月饼"),
    ("端午节纪念的历史人物是谁？", "屈原"),
    ("《论语》主要记录了谁的思想？", "孔子"),
    ("《红楼梦》的作者是谁？", "曹雪芹"),
    ("《西游记》的作者是谁？", "吴承恩"),
    ("万里长城最早大规模修筑开始于哪个朝代？", "秦朝"),
    ("故宫位于哪一座城市？", "北京"),
]

LIFE = [
    ("一年有多少个月？", "12"),
    ("一周有多少天？", "7"),
    ("一天有多少个小时？", "24"),
    ("一小时有多少分钟？", "60"),
    ("一分钟有多少秒？", "60"),
    ("平年一年有多少天？", "365"),
    ("闰年的二月有多少天？", "29"),
    ("一公里等于多少米？", "1000"),
    ("一米等于多少厘米？", "100"),
    ("一千克等于多少克？", "1000"),
    ("一吨等于多少千克？", "1000"),
    ("一小时等于多少秒？", "3600"),
    ("成年人每晚建议的睡眠时长大约是多少小时？", "7|8"),
    ("水在多少摄氏度会结冰？", "0"),
    ("成年人安静状态下的正常心率大约每分钟多少次？", "60|100"),
    ("中国的国庆节是几月几日？", "10月1日"),
    ("春节是农历几月初几？", "正月初一"),
    ("中国法律规定的成年年龄是多少岁？", "18"),
    ("中国的高考通常在几月举行？", "6"),
    ("中国居民身份证号码通常有多少位？", "18"),
]

DOMAIN_TAGS = {
    "地理": "geography",
    "科学": "science",
    "编程": "programming",
    "历史": "history",
    "生活": "life",
    "数学": "math",
}


def _arithmetic_cases(rng):
    cases = []
    for index in range(50):
        operation = index % 3
        if operation == 0:
            left = rng.randint(10, 999)
            right = rng.randint(10, 999)
            question = "计算 %d 加 %d 的结果是多少？" % (left, right)
            answer = left + right
        elif operation == 1:
            left = rng.randint(100, 999)
            right = rng.randint(10, left - 1)
            question = "计算 %d 减 %d 的结果是多少？" % (left, right)
            answer = left - right
        else:
            left = rng.randint(2, 99)
            right = rng.randint(2, 99)
            question = "计算 %d 乘以 %d 的结果是多少？" % (left, right)
            answer = left * right
        cases.append(
            {
                "input": question,
                "expected": str(answer),
                "tags": ["math", "基础" if answer < 1000 else "进阶"],
                "meta": {"domain": "数学", "source": SEED_VERSION},
            }
        )
    return cases


def build_cases():
    rng = random.Random(20240601)
    cases = []
    for country, capital in CAPITALS:
        cases.append(
            {
                "input": "%s的首都是哪座城市？" % country,
                "expected": "%s|%s" % (capital, country),
                "tags": ["geography", "基础"],
                "meta": {"domain": "地理", "source": SEED_VERSION},
            }
        )
    for question, expected in SCIENCE:
        cases.append(
            {
                "input": question,
                "expected": expected,
                "tags": ["science", "进阶"],
                "meta": {"domain": "科学", "source": SEED_VERSION},
            }
        )
    for question, expected in PROGRAMMING:
        cases.append(
            {
                "input": question,
                "expected": expected,
                "tags": ["programming", "进阶"],
                "meta": {"domain": "编程", "source": SEED_VERSION},
            }
        )
    for question, expected in HISTORY:
        cases.append(
            {
                "input": question,
                "expected": expected,
                "tags": ["history", "基础"],
                "meta": {"domain": "历史", "source": SEED_VERSION},
            }
        )
    for question, expected in LIFE:
        cases.append(
            {
                "input": question,
                "expected": expected,
                "tags": ["life", "基础"],
                "meta": {"domain": "生活", "source": SEED_VERSION},
            }
        )
    cases.extend(_arithmetic_cases(rng))
    for index, case in enumerate(cases, start=1):
        case["meta"]["index"] = index
    return cases


def build_targets():
    return [
        {
            "name": "mock-baseline-85",
            "kind": "mock",
            "config": {
                "accuracy": 0.85,
                "salt": "demo-v1",
                "latency_ms": [20, 200],
                "max_steps": 3,
                "price_in_per_1m": 0.15,
                "price_out_per_1m": 0.60,
            },
        },
        {
            "name": "mock-improved-90",
            "kind": "mock",
            "config": {
                "accuracy": 0.90,
                "salt": "demo-v2",
                "latency_ms": [20, 180],
                "max_steps": 3,
                "price_in_per_1m": 0.15,
                "price_out_per_1m": 0.60,
            },
        },
        {
            "name": "mock-slow-timeout",
            "kind": "mock",
            "config": {
                "accuracy": 0.85,
                "salt": "slow-v1",
                "latency_ms": [2000, 2500],
                "max_steps": 3,
                "price_in_per_1m": 0.15,
                "price_out_per_1m": 0.60,
            },
        },
        {
            "name": "http-demo",
            "kind": "http",
            "config": {
                "url": "http://127.0.0.1:8205/api/echo",
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
                "body_template": {"prompt": "{input}"},
                "timeout_s": 10,
                "response_map": {
                    "answer": "answer",
                    "steps": "steps",
                    "tokens_in": "usage.input_tokens",
                    "tokens_out": "usage.output_tokens",
                    "cost_usd": "cost_usd",
                },
            },
        },
        {
            "name": "openai-compatible-demo",
            "kind": "openai-compatible",
            "config": {
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key_env": "OPENAI_API_KEY",
                "temperature": 0,
                "max_tokens": 256,
                "timeout_s": 30,
            },
        },
    ]


def write_files():
    cases = build_cases()
    targets = build_targets()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_path = DATA_DIR / "seed_cases.json"
    jsonl_path = DATA_DIR / "seed_cases.jsonl"
    targets_path = DATA_DIR / "seed_targets.json"
    json_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    jsonl_path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8"
    )
    targets_path.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote %d cases -> %s" % (len(cases), json_path))
    print("wrote %d cases -> %s" % (len(cases), jsonl_path))
    print("wrote %d targets -> %s" % (len(targets), targets_path))
    return cases, targets


DATASET_NAME = "中文问答基准"
DATASET_VERSION = "v1"


def seed_direct(reset=False, verbose=True):
    sys.path.insert(0, str(BASE_DIR))
    from app import db

    if reset:
        db.reset_db()
    else:
        db.init_db()
    existing = db.query_one("SELECT * FROM datasets WHERE name=? AND version=?", (DATASET_NAME, DATASET_VERSION))
    if existing and not reset:
        if verbose:
            print("dataset %r %s already exists (id=%s), skipping case import" % ("中文问答基准", DATASET_VERSION, existing["id"]))
        dataset_id = existing["id"]
    else:
        cases = build_cases()
        dataset_id = db.execute(
            "INSERT INTO datasets (name, version, created_at) VALUES (?,?,?)",
            (DATASET_NAME, DATASET_VERSION, db.now_iso()),
        )
        now = db.now_iso()
        rows = []
        for case in cases:
            expected = case.get("expected", "")
            if not isinstance(expected, str):
                expected = json.dumps(expected, ensure_ascii=False)
            rows.append(
                (
                    dataset_id,
                    case["input"],
                    expected,
                    ",".join(case.get("tags") or []),
                    json.dumps(case.get("meta") or {}, ensure_ascii=False),
                    1,
                    now,
                )
            )
        db.executemany(
            'INSERT INTO cases (dataset_id, "input", expected, tags, meta, active, created_at) '
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        if verbose:
            print("created dataset %r (%s) id=%d with %d cases" % (DATASET_NAME, DATASET_VERSION, dataset_id, len(rows)))
    for target in build_targets():
        found = db.query_one("SELECT id FROM targets WHERE name=?", (target["name"],))
        if found:
            if verbose:
                print("target %-26s exists id=%s" % (target["name"], found["id"]))
            continue
        target_id = db.execute(
            "INSERT INTO targets (name, kind, config, created_at) VALUES (?,?,?,?)",
            (target["name"], target["kind"], db.dumps(target["config"]), db.now_iso()),
        )
        if verbose:
            print("created target %-26s kind=%-18s id=%s" % (target["name"], target["kind"], target_id))
    if verbose:
        print("seed --direct done, db=%s" % db.resolve_db_path())
    return dataset_id


def seed_api(base_url, verbose=True):
    import httpx

    base_url = base_url.rstrip("/")
    cases, targets = build_cases(), build_targets()
    with httpx.Client(timeout=60) as client:
        health = client.get(base_url + "/api/health")
        health.raise_for_status()
        datasets = client.get(base_url + "/api/datasets").json()["items"]
        dataset = next((item for item in datasets if item["name"] == DATASET_NAME), None)
        if dataset is None:
            response = client.post(
                base_url + "/api/datasets",
                json={"name": DATASET_NAME, "version": DATASET_VERSION},
            )
            response.raise_for_status()
            dataset = response.json()
        dataset_id = dataset["id"]
        existing_cases = client.get(base_url + "/api/datasets/%d/cases?limit=1" % dataset_id).json()["total"]
        if existing_cases:
            if verbose:
                print("dataset id=%d already has %d cases, skipping case import" % (dataset_id, existing_cases))
        else:
            response = client.post(
                base_url + "/api/datasets/%d/cases" % dataset_id,
                json={"text": json.dumps(cases, ensure_ascii=False), "format": "json"},
            )
            response.raise_for_status()
            imported = response.json()
            if verbose:
                print(
                    "dataset id=%d: imported=%d skipped=%d total_cases=%d"
                    % (dataset_id, imported["imported"], imported["skipped"], imported["total_cases"])
                )
        existing_targets = {item["name"]: item["id"] for item in client.get(base_url + "/api/targets").json()["items"]}
        target_ids = {}
        for target in targets:
            if target["name"] in existing_targets:
                target_ids[target["name"]] = existing_targets[target["name"]]
                continue
            response = client.post(
                base_url + "/api/targets",
                json={"name": target["name"], "kind": target["kind"], "config": target["config"]},
            )
            response.raise_for_status()
            target_ids[target["name"]] = response.json()["id"]
        if verbose:
            for name, target_id in target_ids.items():
                print("target %-26s id=%s" % (name, target_id))
            print("seed --api done: dataset_id=%d bases=%s" % (dataset_id, target_ids))
    return dataset_id, target_ids


def main():
    parser = argparse.ArgumentParser(description="Generate / import demo data for the evaluation platform")
    parser.add_argument("--direct", action="store_true", help="write demo data straight into SQLite")
    parser.add_argument("--api", default=None, help="import demo data through the REST API of a running server")
    parser.add_argument("--reset", action="store_true", help="with --direct: delete the SQLite database first")
    args = parser.parse_args()
    if args.api:
        seed_api(args.api)
    elif args.direct:
        seed_direct(reset=args.reset)
    else:
        write_files()


if __name__ == "__main__":
    main()
