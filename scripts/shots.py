"""用 Playwright + 本机 Edge 给 README 采集界面截图。

用法： python scripts/shots.py [--url http://127.0.0.1:8321] [--out docs/screenshots] [--skip-gen]
前置：服务已启动，且 scripts/seed_demo.py 已经跑过（演示账号 demo / demo123456）。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

# Playwright 会在系统 temp 下 mkdtemp 存放浏览器产物；某些受限环境里 %TEMP% 不可写，
# 这里把临时目录改到项目内的 .tmp，保证在任何机器上都能跑通。
_TMP = Path(__file__).resolve().parents[1] / ".tmp"
_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = os.environ["TMPDIR"] = str(_TMP)
tempfile.tempdir = str(_TMP)

from playwright.sync_api import sync_playwright  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DEMO_USER = "demo"
DEMO_PASS = "demo123456"

TOPIC = "阅读下面的材料，自选角度，自拟标题，写一篇不少于 800 字的议论文。"
MATERIAL = "《周易》说“穷则变，变则通，通则久”；《论语》说“温故而知新”。故宫用数字化让文物“活”起来，敦煌用“数字供养人”延续千年壁画——传统与创新在当下彼此照亮。"
REQ = "结合材料谈谈你对“守正与创新”的思考；自拟标题，不得抄袭套作。"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8321")
    ap.add_argument("--out", default="docs/screenshots")
    ap.add_argument("--skip-gen", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):  # 重跑时清掉旧图，避免编号错位
        old.unlink()
    shot_no = 0

    def shot(page, name: str, full: bool = False) -> None:
        nonlocal shot_no
        shot_no += 1
        path = out / f"{shot_no:02d}-{name}.png"
        page.screenshot(path=str(path), full_page=full)
        print("  saved", path.name)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True)
        ctx = browser.new_context(viewport={"width": 1560, "height": 1000}, device_scale_factor=2, locale="zh-CN")
        page = ctx.new_page()
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_timeout(800)

        # 1) 未登录时的登录/注册浮层
        page.wait_for_selector("#auth-modal:not(.hidden)", timeout=8000)
        shot(page, "登录注册")

        # 2) 登录后进入工作台
        page.fill("#auth-username", DEMO_USER)
        page.fill("#auth-password", DEMO_PASS)
        page.click("#auth-form button[type=submit]")
        # .hidden 元素不可见，不能用默认的 state="visible"，这里直接等 class 变化
        page.wait_for_function(
            "() => document.getElementById('auth-modal').classList.contains('hidden')", timeout=15000
        )
        page.wait_for_timeout(600)
        shot(page, "工作台首页")

        if not args.skip_gen:
            # 3) 触发生成，抓“生成中”的实时面板
            page.fill("#topic", TOPIC)
            page.fill("#material", MATERIAL)
            page.fill("#requirement", REQ)
            page.select_option("#genre", "议论文")
            page.select_option("#word-count", "800")
            page.check("#do-critique")
            page.click("#start-gen")
            page.wait_for_selector(".stage-item", timeout=20000)
            page.wait_for_timeout(26000)
            shot(page, "生成中-实时阶段")
            print("  等待生成完成…")
            page.wait_for_function(
                "() => { const b = document.getElementById('essay-body');"
                " return b && !b.classList.contains('empty') && b.textContent.length > 600"
                " && document.getElementById('gen-status').textContent.includes('完成'); }",
                timeout=420000,
            )
            page.wait_for_timeout(3200)  # 等“已保存到历史记录”的 toast 淡出，避免挡住正文
            shot(page, "生成完成-评分报告")
            shot(page, "生成完成-整页", full=True)

        # 4) 历史记录
        page.click("#tabs button[data-view=history]")
        page.wait_for_selector("#essay-list li", timeout=15000)
        page.wait_for_timeout(500)
        page.click("#essay-list li")
        page.wait_for_timeout(1200)
        shot(page, "历史记录")

        # 5) 提示词页
        page.click("#tabs button[data-view=prompts]")
        page.wait_for_selector(".prompt-stages .prompt-stage, #prompt-stages > *", timeout=15000)
        page.wait_for_timeout(800)
        shot(page, "核心提示词")
        shot(page, "核心提示词-整页", full=True)

        # 6) 范文素材检索抽屉
        page.click("#tabs button[data-view=workbench]")
        page.wait_for_timeout(400)
        page.click("#search-corpus")
        page.wait_for_timeout(600)
        page.fill("#corpus-query", "坚持 理想 奋斗")
        page.click("#corpus-search-btn")
        page.wait_for_timeout(1800)
        shot(page, "范文素材检索")

        ctx.close()
        browser.close()
    print(f"done: {shot_no} screenshots -> {out}")


if __name__ == "__main__":
    sys.exit(main())
