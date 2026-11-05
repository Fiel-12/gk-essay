"""从 data/raw/*.txt（由 20 份高考满分作文 docx 抽取而来）构建 data/corpus.json。

用法：
    python scripts/build_corpus.py            # 读取 data/raw，写入 data/corpus.json
    python scripts/build_corpus.py --chunk 500
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_FILE = ROOT / "data" / "corpus.json"


def clean(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_chunks(text: str, chunk: int) -> list[str]:
    """按段落聚合到约 chunk 字，避免把一篇范文切碎成无意义碎片。"""
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for p in paras:
        if size + len(p) > chunk and buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(p)
        size += len(p)
    if buf:
        chunks.append("\n".join(buf))
    return [c for c in chunks if len(c) >= 80]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=520)
    ap.add_argument("--raw", default=str(RAW_DIR))
    ap.add_argument("--out", default=str(OUT_FILE))
    args = ap.parse_args()

    raw_dir = Path(args.raw)
    files = sorted(raw_dir.glob("*.txt"))
    if not files:
        raise SystemExit(f"未找到语料文本：{raw_dir}")

    docs: list[dict] = []
    for path in files:
        text = clean(path.read_text(encoding="utf-8", errors="ignore"))
        if not text:
            continue
        base_title = re.sub(r"^\d+_", "", path.stem)
        for idx, chunk in enumerate(split_chunks(text, args.chunk), start=1):
            docs.append(
                {
                    "title": f"{base_title} #{idx}",
                    "source": path.name,
                    "text": chunk,
                }
            )

    payload = {"count": len(docs), "docs": docs}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(d["text"]) for d in docs)
    print(f"wrote {out} docs={len(docs)} chars={total}")


if __name__ == "__main__":
    main()
