"""为 README 截图准备演示数据：创建演示账号并生成 2 篇作文（一篇全流程、一篇快稿）。

用法： python scripts/seed_demo.py
幂等：账号已存在则直接登录；每次运行会再追加 2 篇作文。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8321"
DEMO_USER = "demo"
DEMO_PASS = "demo123456"

TOPICS = [
    {
        "topic": "阅读下面的材料，自选角度，自拟标题，写一篇不少于 800 字的议论文。",
        "material": "《周易》说“穷则变，变则通，通则久”；《论语》说“温故而知新”。故宫用数字化让文物“活”起来，敦煌用“数字供养人”延续千年壁画，传统与创新在当下彼此照亮。",
        "requirement": "结合材料，谈谈你对“守正与创新”的思考；自拟标题，不得抄袭套作。",
        "genre": "议论文",
        "word_count": 800,
        "angle_count": 3,
        "do_critique": True,
    },
    {
        "topic": "以“慢下来，才能看清”为题，写一篇不少于 800 字的散文。",
        "material": "木心说：“从前的日色变得慢，车，马，邮件都慢。”",
        "requirement": "可叙可议，写出自己的真切感受。",
        "genre": "散文",
        "word_count": 800,
        "angle_count": 2,
        "do_critique": False,
    },
]


def call(path: str, payload: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload, ensure_ascii=False).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def login() -> str:
    try:
        return call("/api/auth/register", {"username": DEMO_USER, "password": DEMO_PASS, "nickname": "示范同学"})["token"]
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
    return call("/api/auth/login", {"username": DEMO_USER, "password": DEMO_PASS})["token"]


def generate(token: str, body: dict) -> None:
    req = urllib.request.Request(BASE + "/api/generate", data=json.dumps(body, ensure_ascii=False).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    t0 = time.time()
    title = ""
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev.get("type") == "meta":
                title = ev.get("title", "")
                print(f"   meta: 《{title}》 {ev.get('word_count')} 字 得分 {(ev.get('score') or {}).get('total')}")
            elif ev.get("type") == "saved":
                print(f"   saved id={ev['essay_id']} 用时 {time.time()-t0:.0f}s")
            elif ev.get("type") == "error":
                print("   ERROR:", ev.get("message"))


def main() -> None:
    token = login()
    print("demo token ready")
    for body in TOPICS:
        print("generating:", body["topic"][:24], "...")
        generate(token, body)
    print("done")


if __name__ == "__main__":
    sys.exit(main())
