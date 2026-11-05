# 自检记录（SELFCHECK）

环境：Windows + Python 3.13（venv `.venv`）+ FastAPI 0.162.0 + openai 3.46.0
模型：DeepSeek OpenAI 兼容端点（`deepseek-v4-pro` / `deepseek-flash`）
工作目录：`D:\aigc\gaokao-essay-agent`

本文件只记录**真实执行过**的检查及其输出，未执行的项会明确标注。

---

## 1. 字节编译（静态语法）

```powershell
& .\.venv\Scripts\python.exe -m compileall -q app scripts
```

结果：`exit=0`，`app/` 与 `scripts/` 下全部模块编译通过，无 SyntaxError。

## 2. 轻量接口自检（不调用大模型，秒级）

```powershell
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8123
& .\.venv\Scripts\python.exe scripts\selfcheck.py http://127.0.0.1:8123
```

实测输出：

```
[PASS] health: {"status": "ok", "model": "deepseek-v4-pro"}
[PASS] meta: 班级=网安243 姓名=刘经治 学号=202401050896 语料=298
[PASS] prompts: 阶段=['angle', 'outline', 'essay', 'critique', 'polish'] 策略数=9
[PASS] auth-required: 未登录访问历史返回 401（期望 401）
[PASS] corpus: 片段数=298
[PASS] bm25-search: 命中 3 条，首条相关度 22.11
[PASS] index-html: 6574 字节

RESULT: ALL PASS
```

## 3. 端到端自检（真实调用模型，约 2 分钟）

```powershell
& .\.venv\Scripts\python.exe scripts\smoke_test.py --critique --base http://127.0.0.1:8123
```

实测摘要：

```
health: {"status":"ok","model":"deepseek-v4-pro"}
meta: {"app_title":"高考满分作文智能应用","student_class":"网安243","student_name":"刘经治",
       "student_id":"202401050896","model":"deepseek-v4-pro","corpus_count":298}
register OK user_id = 5
corpus hits: [('有关理想信念的高考满分作文10篇 #5', 30.07), ...]
  [  12.5s] STAGE angle    done (699 chars)
  [  16.3s] STAGE outline  done (484 chars)
  [  96.1s] STAGE essay    done (940 chars)
  [ 103.0s] STAGE critique done (1006 chars)
  [ 113.7s] STAGE polish   done (1087 chars)
event counts: {'stage': 10, 'thinking': 22, 'delta': 3075, 'meta': 1, 'saved': 1, 'done': 1}
elapsed: 113.7s
title: 根柢既固，新枝乃发
word_count: 893
score: 50.0 一类下
history count: 1
export md bytes: 7218 | export docx bytes: 5095 (zip magic: True)
favorite: {'ok': True}
unauth essays -> 401 (expected 401)
```

覆盖：五阶段 SSE、事件协议（stage / thinking / delta / meta / saved / done）、落库、
历史列表、详情、Markdown 与 Word 导出、收藏、未登录 401。

## 4. 截图采集（Playwright + 本机 Edge，headless）

```powershell
& .\.venv\Scripts\python.exe scripts\shots.py
```

实测输出 9 张图到 `docs/screenshots/`：登录页、工作台、生成中、生成完成（视口 / 整页）、
历史记录、提示词页（视口 / 整页）、素材检索。脚本会清空旧图、自动登录 `demo` 账号、
真实触发生成并等待五个阶段完成，因此截图与真实运行状态一致。

## 5. 文档产出

| 产出 | 检查方式 | 结果 |
| --- | --- | --- |
| `docs/核心提示词.md` | `scripts/export_prompts.py` 从 `app/prompts.py` 导出 | 7245 字符，五阶段 system/user 全文 |
| `README.md` | 人工核对 | 含班级/姓名/学号、9 张配图、运行说明 |
| `README.docx` | `check_office.py`（ZIP/XML 完整性、段落、表格、节） | `verdict: pass`，253 段、5 张表、11 页、5.5 MB |

## 6. 已知限制

1. 全流程耗时 100~140 秒：推理模型在「审题」「评分」阶段会先思考（实测思考 6000+ 字），
   这是质量换时间的取舍；可用 `THINKING_STAGES=` 关闭。
2. `README.docx` 的版式只做了结构化校验（python-docx 生成的 OOXML 合法、表格固定列宽、
   图片宽度 15cm < 正文宽度 15.4cm）。本机 LibreOffice 光栅化会整体放大导致裁切
   （用一个最小 docx 复现同样现象），因此未用它做像素级版式验收，建议在 Word 中过目。
3. 语料来自网络收集的范文，个别片段存在原文排版噪声（如重复标题、断句），
   只作为灵感素材注入，不参与成文。
