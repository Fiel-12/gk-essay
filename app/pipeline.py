"""五阶段生成流水线：把 prompts.py 的提示词串成真正的多阶段调用，并产出 SSE 事件流。

事件契约（与 static/app.js 对齐）：
    {"type":"stage","stage":key,"status":"start"|"done","title":...,"content":...}
    {"type":"delta","stage":key,"text":"..."}
    {"type":"meta","title":...,"content":...,"outline":...,"critique":...,"score":{...}}
"""
from __future__ import annotations

import json
import re
from typing import AsyncIterator

import os

from . import prompts
from .config import settings
from .llm import LLMError, stream_chat_rich
from .schemas import GenerateRequest

# 分阶段温度调度：稳定的用低温，要文采的用高温
_TEMPERATURE = {"angle": 0.35, "outline": 0.55, "essay": 0.95, "critique": 0.2, "polish": 0.75}
# 输出预算：模型会先花 token 思考再写正文，开思考的阶段必须给足额度，
# 否则额度和思考一起耗尽、正文会是空字符串（实测踩过 outline/critique 双空）。
_MAX_TOKENS = {"angle": 4000, "outline": 2600, "essay": 4200, "critique": 8192, "polish": 4200}
# 模型分工：审题/提纲用快模型就够，成篇/评分/润色用主模型保质量
_MODEL_STAGE = {"angle": "fast", "outline": "fast", "essay": "main", "critique": "main", "polish": "main"}

# 思考开关：模型是推理模型，思考会显著提升质量但会推迟首字，
# 所以“要想清楚”的阶段开、长文输出的阶段默认关（可用 .env 的 THINKING_STAGES 覆盖）。
_DEFAULT_THINKING_STAGES = {"angle", "critique"}
_env_thinking = os.environ.get("THINKING_STAGES", "").strip()
_THINKING = {
    stage: (stage in (_env_thinking.split(",") if _env_thinking else _DEFAULT_THINKING_STAGES))
    for stage in _TEMPERATURE
}

_CJK = re.compile(r"[\u4e00-\u9fff]")


def count_words(text: str) -> int:
    """高考作文字数：中文字符逐个计数，连续的西文/数字按词计。"""
    cjk = len(_CJK.findall(text))
    latin = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + latin


def _retrieve(req: GenerateRequest, top_k: int = 3) -> list[str]:
    """RAG-lite：从本地范文库取相关片段。corpus 不可用时静默降级为空。"""
    try:
        from .corpus import bm25_search  # 延迟导入，避免循环依赖

        query = " ".join(x for x in [req.topic, req.requirement, req.material[:200]] if x)
        hits = bm25_search(query, top_k=top_k) or []
        return [h.get("text", "") for h in hits if h.get("text")]
    except Exception:  # noqa: BLE001 - 检索是增强项，失败不影响生成
        return []


def extract_title(text: str) -> str:
    """从成稿里抠标题：优先“标题：xxx”，否则取首个非空行。"""
    for line in text.splitlines():
        line = line.strip().strip("*# ").strip()
        if not line:
            continue
        m = re.match(r"^标\s*题\s*[:：]\s*(.+)$", line)
        if m:
            return m.group(1).strip().strip("《》")
        if len(line) <= 30 and not line.endswith(("。", "：", ":")):
            return line.strip("《》")
        break
    return ""


def strip_title_line(text: str) -> str:
    """去掉开头的“标题：xxx”行，正文从第一个空行或第一段开始。"""
    lines = text.splitlines()
    out: list[str] = []
    stripped = False
    for line in lines:
        s = line.strip()
        if not stripped and re.match(r"^标\s*题\s*[:：]", s):
            stripped = True
            continue
        if stripped and not s and not out:
            continue
        out.append(line)
    return "\n".join(out).strip()


def parse_score(raw: str) -> dict:
    """尽最大努力把评分 JSON 抠出来；失败时返回带 error 的兜底结构。"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {"error": "评分结果不是合法 JSON", "raw": raw[:800]}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {"error": "评分 JSON 解析失败", "raw": raw[:800]}

    def num(value, default: float, lo: float, hi: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, round(v, 1)))

    base = data.get("base") or {}
    dev = data.get("development") or {}
    score = {
        "total": num(data.get("total"), 0, 0, 60),
        "base": {
            "content": num(base.get("content"), 0, 0, 20),
            "expression": num(base.get("expression"), 0, 0, 20),
        },
        "development": {
            "deep": num(dev.get("deep"), 0, 0, 5),
            "rich": num(dev.get("rich"), 0, 0, 5),
            "literary": num(dev.get("literary"), 0, 0, 5),
            "creative": num(dev.get("creative"), 0, 0, 5),
        },
        "verdict": str(data.get("verdict", "")),
        "highlights": [str(x) for x in (data.get("highlights") or [])][:5],
        "flaws": [str(x) for x in (data.get("flaws") or [])][:5],
        "suggestions": [str(x) for x in (data.get("suggestions") or [])][:5],
        "comment": str(data.get("comment", "")),
    }
    if not score["total"]:
        score["total"] = round(
            score["base"]["content"]
            + score["base"]["expression"]
            + sum(score["development"].values()),
            1,
        )
    return score


def _stage_model(stage: str) -> str:
    return settings.fast_model if _MODEL_STAGE.get(stage) == "fast" else settings.model


async def _run_stage(stage: str, messages: list[dict]) -> AsyncIterator[dict]:
    yield {"type": "stage", "stage": stage, "status": "start", "title": prompts.STAGE_TITLES[stage]}
    wants_thinking = _THINKING.get(stage, True)
    # 第一次按配置来；如果思考把输出预算吃光导致正文为空，就关掉思考重跑一次兜底
    attempts = [wants_thinking, False] if wants_thinking else [False]
    content = ""
    thinking_chars = 0
    for idx, thinking in enumerate(attempts):
        if idx > 0:
            yield {"type": "thinking", "stage": stage, "chars": thinking_chars, "retry": True}
        buf: list[str] = []
        reported = thinking_chars
        async for kind, text in stream_chat_rich(
            messages,
            model=_stage_model(stage),
            temperature=_TEMPERATURE.get(stage, 0.7),
            max_tokens=_MAX_TOKENS.get(stage, 2600),
            thinking=thinking,
        ):
            if kind == "reasoning":
                # 思考过程不直接展示，只报进度，避免前端长时间空白
                thinking_chars += len(text)
                if thinking_chars - reported >= 300:
                    reported = thinking_chars
                    yield {"type": "thinking", "stage": stage, "chars": thinking_chars}
                continue
            buf.append(text)
            yield {"type": "delta", "stage": stage, "text": text}
        content = "".join(buf).strip()
        if content:
            break
    yield {"type": "stage", "stage": stage, "status": "done", "content": content, "thinking_chars": thinking_chars}
    yield {"__collected__": stage, "content": content}


async def run_pipeline(req: GenerateRequest) -> AsyncIterator[dict]:
    """按“审题→提纲→成篇→自检→润色”顺序执行，边执行边推事件。"""
    snippets = _retrieve(req)
    collected: dict[str, str] = {}

    order = ["angle", "outline", "essay"]
    if req.do_critique:
        order += ["critique", "polish"]

    try:
        # ---- 1. 审题立意 ----
        async for event in _run_stage("angle", prompts.build_angle_prompt(req, snippets)):
            if "__collected__" in event:
                collected["angle"] = event["content"]
            else:
                yield event
        angle = collected.get("angle", "")

        # ---- 2. 结构提纲 ----
        async for event in _run_stage("outline", prompts.build_outline_prompt(req, angle)):
            if "__collected__" in event:
                collected["outline"] = event["content"]
            else:
                yield event
        outline = collected.get("outline", "")

        # ---- 3. 正文成篇 ----
        async for event in _run_stage("essay", prompts.build_essay_prompt(req, outline, snippets)):
            if "__collected__" in event:
                collected["essay"] = event["content"]
            else:
                yield event
        draft = collected.get("essay", "")

        # ---- 4. 评分自检 ----
        score: dict = {}
        final_text = draft
        if req.do_critique and draft:
            async for event in _run_stage("critique", prompts.build_critique_prompt(req, draft)):
                if "__collected__" in event:
                    collected["critique"] = event["content"]
                else:
                    yield event
            critique_raw = collected.get("critique", "")
            score = parse_score(critique_raw)

            # ---- 5. 润色定稿 ----
            if critique_raw:
                async for event in _run_stage(
                    "polish", prompts.build_polish_prompt(req, draft, critique_raw)
                ):
                    if "__collected__" in event:
                        collected["polish"] = event["content"]
                    else:
                        yield event
                if collected.get("polish"):
                    final_text = collected["polish"]

        body = strip_title_line(final_text)
        title = extract_title(final_text) or extract_title(draft) or req.topic[:20]

        yield {
            "type": "meta",
            "title": title,
            "content": body,
            "outline": outline,
            "critique": collected.get("critique", ""),
            "score": score,
            "word_count": count_words(body),
            "genre": req.genre,
            "topic": req.topic,
            "snippets_used": len(snippets),
        }
    except LLMError as exc:
        yield {"type": "error", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 兜底，避免流内异常直接断开
        yield {"type": "error", "message": f"生成失败：{exc}"}


__all__ = [
    "run_pipeline",
    "parse_score",
    "extract_title",
    "strip_title_line",
    "count_words",
]
