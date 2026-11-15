import time

import httpx

BASE = "http://127.0.0.1:8205"
for _ in range(60):
    try:
        response = httpx.get(BASE + "/api/health", timeout=2)
        if response.status_code == 200:
            print("server ready:", response.json()["status"])
            break
    except Exception:
        pass
    time.sleep(0.4)
else:
    raise SystemExit("server did not become ready")
