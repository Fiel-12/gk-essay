"""把作文导出成最小可用的 DOCX（OOXML）字节流。

为什么手写 zipfile 而不是引入 python-docx：
1. 依赖清单被规格锁死，python-docx 属于越界。
2. Word 的 .docx 本质是一个 zip 包 + 固定几个 XML，最小可打开包只要
   [Content_Types].xml、_rels/.rels、word/document.xml、word/_rels/document.xml.rels
   四个成员即可，用标准库 zipfile 写起来可控且无额外负担。
3. 中文统一按 UTF-8 写入并在 XML 头声明，避免 Windows 上默认编码差异导致乱码。
"""
from __future__ import annotations

import io
import zipfile
from typing import Any
from xml.sax.saxutils import escape

_NS_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _esc(text: str) -> str:
    """XML 文本转义：正文里出现 <>& 等字符时才不会破坏文档结构。"""
    return escape(str(text), {'"': "&quot;", "'": "&apos;"})


def _paragraph(text: str, bold: bool = False, size: str | None = None) -> str:
    """把一段文字转成 <w:p>；每行一个段落，空行保留为空白段落。"""
    runs = []
    if bold:
        runs.append("<w:b/>")
    if size:
        runs.append(f'<w:sz w:val="{size}"/>')
    rpr = f"<w:rPr>{''.join(runs)}</w:rPr>" if runs else ""
    return f"<w:p><w:r>{rpr}<w:t xml:space=\"preserve\">{_esc(text)}</w:t></w:r></w:p>"


def _content_types() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )


def _doc_rels() -> str:
    # 没有任何图片/超链接关系，空的 Relationships 也是合法包成员
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )


def essay_to_docx_bytes(title: str, meta: dict[str, Any] | None, content: str) -> bytes:
    """生成 DOCX 字节。meta 可含 topic/genre/word_count/outline/critique/score 等展示字段。"""
    meta = meta or {}
    paras: list[str] = []

    # 标题用加大加粗的段落，正文小节用加粗小标题，内容逐行成段
    paras.append(_paragraph(title or "未命名作文", bold=True, size="36"))
    paras.append(_paragraph(""))

    def add_section(name: str, body: Any) -> None:
        text = "" if body is None else str(body).strip()
        if not text:
            return
        paras.append(_paragraph(name, bold=True))
        for line in text.splitlines():
            paras.append(_paragraph(line))
        paras.append(_paragraph(""))

    if meta.get("topic"):
        add_section("题目", meta["topic"])
    if meta.get("genre"):
        add_section("文体", meta["genre"])
    if meta.get("word_count"):
        add_section("字数", meta["word_count"])
    if meta.get("created_at"):
        add_section("生成时间", meta["created_at"])

    add_section("正文", content)
    if meta.get("outline"):
        add_section("结构提纲", meta["outline"])
    if meta.get("critique"):
        add_section("评分自检", meta["critique"])
    score = meta.get("score")
    if isinstance(score, dict) and score:
        add_section("评分", _format_score(score))

    body_xml = "".join(paras)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_NS_W}><w:body>{body_xml}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types())
        zf.writestr("_rels/.rels", _root_rels())
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", _doc_rels())
    return buf.getvalue()


def _format_score(score: dict[str, Any]) -> str:
    """把评分 JSON 压成几行易读文字，避免在 Word 里塞原始 JSON。"""
    lines = [f"总分：{score.get('total', '—')}（{score.get('verdict', '')}）"]
    base = score.get("base") or {}
    dev = score.get("development") or {}
    lines.append(f"基础等级：内容 {base.get('content', '—')} / 表达 {base.get('expression', '—')}")
    lines.append(
        "发展等级：深刻 {} / 丰富 {} / 有文采 {} / 有创意 {}".format(
            dev.get("deep", "—"),
            dev.get("rich", "—"),
            dev.get("literary", "—"),
            dev.get("creative", "—"),
        )
    )
    for name, key in (("亮点", "highlights"), ("硬伤", "flaws"), ("修改建议", "suggestions")):
        items = score.get(key) or []
        if items:
            lines.append(f"{name}：" + "；".join(str(x) for x in items))
    if score.get("comment"):
        lines.append(f"评语：{score['comment']}")
    return "\n".join(lines)


__all__ = ["essay_to_docx_bytes"]
