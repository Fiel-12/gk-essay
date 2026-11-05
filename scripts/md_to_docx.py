"""把 README.md 渲染成 README.docx（交给老师的那一份）。

只支持本项目 README 用到的 Markdown 子集：标题、段落、无序/有序列表、表格、
围栏代码块、图片、行内 **粗体** / `代码` / [链接](url) / > 引用。

用法：
    <python> scripts/md_to_docx.py README.md README.docx
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BODY_FONT = "宋体"
BODY_FONT_LATIN = "Times New Roman"
CODE_FONT = "Consolas"
HEAD_COLOR = RGBColor(0x1F, 0x3B, 0x63)
CODE_BG = "F4F5F7"
CONTENT_WIDTH = Cm(15.0)

INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]*\]\([^)]*\))")


def set_run_font(run, latin: str, east: str, size: float | None = None, bold: bool | None = None):
    run.font.name = latin
    run._element.rPr.rFonts.set(qn("w:eastAsia"), east)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold


def shade(paragraph, fill: str = CODE_BG):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pPr.append(shd)


def add_inline(paragraph, text: str, size: float = 10.5, color: RGBColor | None = None):
    """按行内语法拆成若干 run。"""
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            run = paragraph.add_run(piece[2:-2])
            set_run_font(run, BODY_FONT_LATIN, BODY_FONT, size, bold=True)
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            run = paragraph.add_run(piece[1:-1])
            set_run_font(run, CODE_FONT, CODE_FONT, size - 1)
            run.font.color.rgb = RGBColor(0xA3, 0x1D, 0x4B)
        elif piece.startswith("[") and "](" in piece:
            label, url = piece[1:-1].split("](", 1)
            run = paragraph.add_run(label)
            set_run_font(run, BODY_FONT_LATIN, BODY_FONT, size)
            run.font.color.rgb = RGBColor(0x1A, 0x5F, 0xB4)
            if not label.startswith("!"):
                tail = paragraph.add_run(f"（{url}）")
                set_run_font(tail, BODY_FONT_LATIN, BODY_FONT, size - 1)
                tail.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
        else:
            run = paragraph.add_run(piece)
            set_run_font(run, BODY_FONT_LATIN, BODY_FONT, size)
        if color is not None:
            run.font.color.rgb = color


def build(md_path: Path, out_path: Path):
    lines = md_path.read_text(encoding="utf-8").splitlines()
    doc = Document()

    # 页面与正文默认样式
    section = doc.sections[0]
    section.left_margin = section.right_margin = Cm(2.6)
    section.top_margin = section.bottom_margin = Cm(2.2)
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT_LATIN
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing = 1.25

    i = 0
    first_h1 = True
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # 引用块里的表格（"> | a | b |"）按表格处理
        if stripped.startswith("> ") and stripped[2:].strip().startswith("|"):
            stripped = stripped[2:].strip()

        # 围栏代码块
        if stripped.startswith("```"):
            i += 1
            block: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            for code_line in block:
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.0
                p.paragraph_format.left_indent = Cm(0.4)
                shade(p)
                run = p.add_run(code_line if code_line.strip() else " ")
                set_run_font(run, CODE_FONT, CODE_FONT, 9)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
            continue

        # 表格
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            table = doc.add_table(rows=1, cols=len(header))
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.autofit = False
            col_w = Cm(15.0 / max(1, len(header)))
            for idx, cell_text in enumerate(header):
                cell = table.rows[0].cells[idx]
                cell.text = ""
                add_inline(cell.paragraphs[0], cell_text, size=9.5, color=HEAD_COLOR)
                for run in cell.paragraphs[0].runs:
                    run.font.bold = True
            for row in rows:
                cells = table.add_row().cells
                for idx in range(len(header)):
                    text = row[idx] if idx < len(row) else ""
                    cells[idx].text = ""
                    add_inline(cells[idx].paragraphs[0], text, size=9.5)
            for row in table.rows:  # 固定列宽，避免表格超出正文宽度
                for idx, cell in enumerate(row.cells):
                    cell.width = col_w
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
            continue

        # 图片
        img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if img:
            alt, src = img.group(1), img.group(2)
            path = (md_path.parent / src).resolve()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(6)
            if path.exists():
                p.add_run().add_picture(str(path), width=CONTENT_WIDTH)
            cap = doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap.paragraph_format.space_after = Pt(10)
            add_inline(cap, alt or path.name, size=9, color=RGBColor(0x66, 0x66, 0x66))
            i += 1
            continue

        # 标题
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            if level == 1 and first_h1:
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_after = Pt(14)
                add_inline(p, text, size=20, color=HEAD_COLOR)
                for run in p.runs:
                    run.font.bold = True
                first_h1 = False
            else:
                size = {2: 15, 3: 13, 4: 11.5}.get(level, 11)
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(10)
                p.paragraph_format.space_after = Pt(6)
                add_inline(p, text, size=size, color=HEAD_COLOR)
                for run in p.runs:
                    run.font.bold = True
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            text = stripped.lstrip(">").strip()
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.6)
            add_inline(p, text, size=10, color=RGBColor(0x44, 0x44, 0x44))
            i += 1
            continue

        # 分隔线
        if stripped in ("---", "***", "___"):
            p = doc.add_paragraph()
            pPr = p._p.get_or_add_pPr()
            border = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "D0D5DD")
            border.append(bottom)
            pPr.append(border)
            i += 1
            continue

        # 列表
        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if bullet or numbered:
            if numbered:
                # 用字面编号 + 悬挂缩进，避免 Word 把全文有序列表连成一个编号流
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.9)
                p.paragraph_format.first_line_indent = Cm(-0.55)
                p.paragraph_format.space_after = Pt(3)
                add_inline(p, f"{numbered.group(1)}.\u00a0{numbered.group(2)}")
            else:
                p = doc.add_paragraph(style="List Bullet")
                p.paragraph_format.space_after = Pt(3)
                add_inline(p, bullet.group(1))
            i += 1
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 普通段落（连续行合并）
        buf = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", "|", ">", "```", "- ", "* ", "---"))
                    or re.match(r"^\d+[.)]\s", nxt) or re.match(r"^!\[.*\]\(.*\)$", nxt)):
                break
            buf.append(nxt)
            i += 1
        p = doc.add_paragraph()
        add_inline(p, " ".join(buf))

    doc.core_properties.title = "高考满分作文智能应用 - 运行说明书"
    doc.core_properties.author = "网安243 刘经治 202401050896"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    print(f"written: {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    md = Path(sys.argv[1] if len(sys.argv) > 1 else "README.md")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "README.docx")
    build(md, out)
