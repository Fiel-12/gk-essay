"""端到端联调脚本：注册 → 生成（SSE）→ 历史 → 导出 → 检索。

用法： python scripts/smoke_test.py [--base http://127.0.0.1:8321] [--nogen]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid


def post(base: str, path: str, payload: dict, token: str | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(base + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get(base: str, path: str, token: str | None = None, timeout: int = 30):
    req = urllib.request.Request(base + path)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8123")
    ap.add_argument("--nogen", action="store_true")
    ap.add_argument("--critique", action="store_true")
    args = ap.parse_args()
    base = args.base

    raw, _ = get(base, "/api/health")
    print("health:", raw.decode())
    raw, _ = get(base, "/api/meta")
    print("meta:", raw.decode()[:160])

    # 1) 注册
    username = "tester_" + uuid.uuid4().hex[:6]
    reg = post(base, "/api/auth/register", {"username": username, "password": "test123456", "nickname": "联调用户"})
    token = reg["token"]
    print("register OK user_id =", reg["user"]["id"])

    # 2) 检索
    s = post(base, "/api/corpus/search", {"query": "坚持 理想 奋斗", "top_k": 3})
    print("corpus hits:", [(r["doc_title"], round(r["score"], 2)) for r in s["results"]])

    if args.nogen:
        return

    # 3) 生成（SSE）
    body = {
        "topic": "以“守正与创新”为题，写一篇不少于 800 字的议论文",
        "material": "《论语》讲“温故而知新”，《周易》讲“穷则变，变则通”。",
        "requirement": "自选角度，自拟标题；不要套作，不得抄袭；不少于 800 字。",
        "genre": "议论文",
        "word_count": 800,
        "angle_count": 3,
        "do_critique": bool(args.critique),
    }
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(base + "/api/generate", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)

    t0 = time.time()
    counts: dict[str, int] = {}
    essay_id = None
    meta = None
    essay_text = ""
    stage_done: dict[str, str] = {}
    with urllib.request.urlopen(req, timeout=900) as resp:
        buf = b""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                line = frame.decode("utf-8", "ignore").strip()
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                t = ev.get("type")
                counts[t] = counts.get(t, 0) + 1
                if t == "stage" and ev.get("status") == "start":
                    print(f"  [{time.time()-t0:6.1f}s] STAGE {ev['stage']} start")
                elif t == "stage" and ev.get("status") == "done":
                    stage_done[ev["stage"]] = ev.get("content", "")
                    print(f"  [{time.time()-t0:6.1f}s] STAGE {ev['stage']} done ({len(ev.get('content',''))} chars)")
                elif t == "thinking":
                    if counts["thinking"] % 8 == 1:
                        print(f"  [{time.time()-t0:6.1f}s] thinking {ev['chars']} chars")
                elif t == "meta":
                    meta = ev
                    essay_text = ev.get("content", "")
                elif t == "saved":
                    essay_id = ev["essay_id"]
                elif t == "error":
                    print("  ERROR EVENT:", ev["message"])
                elif t == "done":
                    print(f"  [{time.time()-t0:6.1f}s] DONE")

    print("event counts:", counts)
    print("elapsed: %.1fs" % (time.time() - t0))
    if meta:
        print("title:", meta.get("title"))
        print("word_count:", meta.get("word_count"))
        score = meta.get("score") or {}
        print("score:", score.get("total"), score.get("verdict"))
        print("--- essay head ---")
        print(essay_text[:300].replace("\n", " / "))

    # 4) 历史 + 详情
    raw, _ = get(base, "/api/essays?limit=5", token)
    items = json.loads(raw)["items"]
    print("history count:", len(items), "first:", items[0]["title"] if items else None)

    if essay_id:
        raw, _ = get(base, f"/api/essays/{essay_id}", token)
        detail = json.loads(raw)
        print("detail content len:", len(detail["content"]), "| score:", (detail.get("score_json") or {}).get("total"))

        # 5) 导出
        raw, headers = get(base, f"/api/essays/{essay_id}/export?fmt=md", token)
        print("export md bytes:", len(raw), "| head:", raw[:40].decode("utf-8", "ignore").replace("\n", " "))
        raw, headers = get(base, f"/api/essays/{essay_id}/export?fmt=docx", token)
        print("export docx bytes:", len(raw), "| zip magic:", raw[:2] == b"PK")
        open(r"D:\aigc\_src\out_test.docx", "wb").write(raw)

        # 6) 收藏
        print("favorite:", post(base, f"/api/essays/{essay_id}/favorite", {"favorite": True}, token))

    try:
        get(base, "/api/essays", None)
        print("unauth essays: NO 401 (BAD)")
    except urllib.error.HTTPError as e:
        print("unauth essays ->", e.code, "(expected 401)")


if __name__ == "__main__":
    main()
