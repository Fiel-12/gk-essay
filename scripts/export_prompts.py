"""把 prompts.py 里真实使用的提示词导出成 Markdown（docs/核心提示词.md）。

用途：实验报告 / GitHub 仓库要提交“项目实现的核心提示词”，
这里直接从前端同一份 PROMPT_SPECS 渲染，保证文档与代码永不漂移。

用法： python scripts/export_prompts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import prompts  # noqa: E402

OUT = ROOT / "docs" / "核心提示词.md"


def main() -> None:
    lines: list[str] = []
    lines.append("# 核心提示词（Prompt Engineering 全记录）")
    lines.append("")
    lines.append(
        "本文件由 `scripts/export_prompts.py` 从 `app/prompts.py` 自动导出，"
        "与系统运行时实际发给大模型的提示词**完全一致**（示例题目为固定 demo 输入）。"
    )
    lines.append("")
    lines.append("## 一、总览：五个阶段的分工")
    lines.append("")
    lines.append("| 阶段 | 目标 | 温度 | 模型 | 思考 |")
    lines.append("| --- | --- | --- | --- | --- |")
    meta = {
        "angle": ("读懂材料，产出 3 个可写的立意角度", "0.35", "deepseek-flash", "开"),
        "outline": ("把中选角度展开成可执行的分段提纲", "0.55", "deepseek-flash", "关"),
        "essay": ("按提纲写出 800 字正文", "0.95", "deepseek-v4-pro", "关"),
        "critique": ("按高考评分标准自评并给分（JSON）", "0.20", "deepseek-v4-pro", "开"),
        "polish": ("按自评意见定向润色，产出定稿", "0.75", "deepseek-v4-pro", "关"),
    }
    for spec in prompts.PROMPT_SPECS:
        goal, temp, model, think = meta.get(spec["key"], ("", "", "", ""))
        lines.append(f"| {spec['title']} | {goal} | {temp} | {model} | {think} |")
    lines.append("")

    lines.append("## 二、用到的提示工程策略")
    lines.append("")
    for i, s in enumerate(prompts.STRATEGIES, 1):
        name = s.get("name", "")
        desc = s.get("desc") or s.get("description") or ""
        where = s.get("where") or s.get("stage") or ""
        lines.append(f"{i}. **{name}**：{desc}" + (f"（落点：{where}）" if where else ""))
    lines.append("")

    lines.append("## 三、五个阶段的完整提示词")
    lines.append("")
    for spec in prompts.PROMPT_SPECS:
        lines.append(f"### {spec['title']}")
        lines.append("")
        lines.append("**System Prompt**")
        lines.append("")
        lines.append("```text")
        lines.append(spec["system"].strip())
        lines.append("```")
        lines.append("")
        lines.append("**User Prompt（模板，`{}` 处由运行时填充）**")
        lines.append("")
        lines.append("```text")
        lines.append(spec["user_template"].strip())
        lines.append("```")
        lines.append("")

    lines.append("## 四、负向约束清单（黑名单）")
    lines.append("")
    lines.append(
        "下面这些词句是“AI 味 / 套作味”的高发区，在所有阶段都被显式禁止："
        "“在当今社会”“综上所述”“赋能/闭环/底层逻辑”等空转词；"
        "机械小标题（一、二、三）；"
        "“屈原—司马迁—爱迪生”万能素材三件套；"
        "排比通胀（连续三段同构排比）；"
        "以及一切 AI 元话语（“作为 AI”“以下是作文”）。"
    )
    lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"written: {OUT} ({len(''.join(lines))} chars)")


if __name__ == "__main__":
    main()
