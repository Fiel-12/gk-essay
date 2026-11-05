"""轻量自检：只查健康、元信息、鉴权与语料，不调用大模型（秒级完成）。

完整端到端自检（会真实生成作文）见 scripts/smoke_test.py --critique。
用法： <python> scripts/selfcheck.py [base_url]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"


def call(method: str, path: str, body: dict | None = None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def main() -> int:
    ok = True

    def report(name: str, passed: bool, detail: str):
        nonlocal ok
        ok = ok and passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    status, health = call("GET", "/api/health")
    report("health", status == 200 and health.get("status") == "ok", json.dumps(health, ensure_ascii=False))

    status, meta = call("GET", "/api/meta")
    report(
        "meta",
        status == 200 and meta.get("student_id") and meta.get("corpus_count", 0) > 0,
        f"班级={meta.get('student_class')} 姓名={meta.get('student_name')} "
        f"学号={meta.get('student_id')} 语料={meta.get('corpus_count')}",
    )

    status, prompts = call("GET", "/api/prompts")
    stages = [p.get("key") for p in (prompts.get("stages") or [])]
    report("prompts", status == 200 and len(stages) == 5, f"阶段={stages} 策略数={len(prompts.get('strategies') or [])}")

    status, _ = call("GET", "/api/essays")
    report("auth-required", status == 401, f"未登录访问历史返回 {status}（期望 401）")

    status, stats = call("GET", "/api/corpus/stats")
    report("corpus", status == 200 and stats.get("count", 0) > 0, f"片段数={stats.get('count')}")

    status, hits = call("POST", "/api/corpus/search", {"query": "坚守理想 奋斗", "top_k": 3})
    report(
        "bm25-search",
        status == 200 and len(hits.get("results") or []) > 0,
        f"命中 {len(hits.get('results') or [])} 条，首条相关度 {round((hits.get('results') or [{}])[0].get('score', 0), 2)}",
    )

    # 静态页面
    with urllib.request.urlopen(BASE + "/", timeout=15) as resp:
        html = resp.read().decode("utf-8", "ignore")
    report("index-html", resp.status == 200 and "高考满分作文智能应用" in html, f"{len(html)} 字节")

    print("\nRESULT:", "ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
