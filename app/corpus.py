"""范文库检索（RAG-lite）：加载语料 + 朴素 BM25 打分。

为什么不用分词库：
1. 依赖限定只能引标准库，jieba 等属于越界。
2. 中文检索用「单字 + 双字」切分能同时覆盖精确词与相邻搭配，
   对「坚持 理想」这类短查询足够好，且零词典、零启动成本。
BM25 参数固定 k1=1.5、b=0.75，符合 SPEC 第 4 节约定。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .config import settings

_K1 = 1.5
_B = 0.75
_CJK = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[a-z0-9]+", re.IGNORECASE)

# 模块级缓存：语料是只读数据，进程生命周期内解析一次即可，省掉每次检索的磁盘 IO
_corpus: list[dict[str, str]] | None = None
_index: dict[str, Any] | None = None


def _tokenize(text: str) -> list[str]:
    """把文本切成词项：汉字出单字与相邻双字，连续西文/数字整串作为一个词。

    双字能捕捉「理想」「坚持」这类常用搭配，单字兜底保证命中单个关键概念，
    两者合并即可在不分词的前提下得到可用的词项集合。
    """
    tokens: list[str] = []
    chars = list(text)
    i = 0
    n = len(chars)
    while i < n:
        ch = chars[i]
        if _CJK.match(ch):
            tokens.append(ch)
            if i + 1 < n and _CJK.match(chars[i + 1]):
                tokens.append(ch + chars[i + 1])
            i += 1
        else:
            m = _LATIN.match(text, i)
            if m:
                tokens.append(m.group(0))
                i = m.end()
            else:
                i += 1
    return tokens


def load_corpus() -> list[dict[str, str]]:
    """读取 data/corpus.json 的 docs 列表；文件缺失时返回空列表而不是崩溃。"""
    global _corpus
    if _corpus is not None:
        return _corpus
    path = Path(settings.corpus_path)
    if not path.exists():
        _corpus = []
        return _corpus
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        docs = data.get("docs") or []
        _corpus = [{"title": str(d.get("title", "")), "text": str(d.get("text", ""))} for d in docs]
    except (json.JSONDecodeError, OSError):
        _corpus = []
    return _corpus


def _build_index() -> dict[str, Any]:
    """构建倒排统计：每篇的词频、文档频率、平均长度。只在第一次检索时惰性构建。"""
    global _index
    if _index is not None:
        return _index
    docs = load_corpus()
    doc_tokens: list[list[str]] = []
    df: dict[str, int] = {}
    for d in docs:
        tokens = _tokenize(d["text"])
        doc_tokens.append(tokens)
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    avgdl = (sum(len(t) for t in doc_tokens) / len(doc_tokens)) if doc_tokens else 0.0
    _index = {
        "docs": docs,
        "doc_tokens": doc_tokens,
        "df": df,
        "avgdl": avgdl,
        "n": len(docs),
    }
    return _index


def bm25_search(query: str, top_k: int = 4) -> list[dict[str, Any]]:
    """按 BM25 返回 top_k 个相关语料段，结果含 doc_title/text/score。"""
    idx = _build_index()
    if not idx["n"]:
        return []
    query_terms = _tokenize(query or "")
    if not query_terms:
        return []

    n = idx["n"]
    df = idx["df"]
    avgdl = idx["avgdl"]
    doc_tokens = idx["doc_tokens"]
    docs = idx["docs"]

    # 先算每个查询词的 IDF，命中太广的词（df==N）信息量接近 0
    idf: dict[str, float] = {}
    for term in set(query_terms):
        dft = df.get(term, 0)
        idf[term] = math.log((n - dft + 0.5) / (dft + 0.5) + 1.0)

    scored: list[tuple[float, int]] = []
    for i, tokens in enumerate(doc_tokens):
        length = len(tokens) or 1
        # 词频统计只需统计查询词，避免对全文做全量 Counter
        tf: dict[str, int] = {}
        for t in tokens:
            if t in idf:
                tf[t] = tf.get(t, 0) + 1
        if not tf:
            continue
        score = 0.0
        norm = 1.0 - _B + _B * (length / avgdl)
        for term, f in tf.items():
            score += idf[term] * (f * (_K1 + 1.0)) / (f + _K1 * norm)
        if score > 0:
            scored.append((score, i))

    # 按分数降序，同分时按文档序降序（更靠后的段落优先），排序结果稳定可复现
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    results: list[dict[str, Any]] = []
    for score, i in scored[:top_k]:
        results.append(
            {
                "doc_title": docs[i]["title"],
                "text": docs[i]["text"],
                "score": round(score, 4),
            }
        )
    return results


__all__ = ["load_corpus", "bm25_search"]
