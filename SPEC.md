# 项目实现规格书（交付给编码智能体执行）

## 0. 你现在要做什么

你是一个编码智能体。请在当前目录（`D:\aigc\gaokao-essay-agent`）实现一个 **FastAPI + 原生前端** 的
「高考满分作文智能应用」。仓库里已经存在一部分文件（见第 5 节“已存在，禁止修改”），
**你只创建第 6 节列出的文件**，并保证整个项目能真实启动、真实跑通。

禁止：修改已存在文件的接口签名；引入第 2 节以外的第三方依赖；使用前端构建工具或 CDN；
使用 `as any` 之类的偷懒写法（Python 无此问题，但禁止 `except: pass`）。

## 1. 产品功能（必须全部实现）

1. **作文生成工作台**：用户输入「作文题目 / 材料 / 写作要求」，可选文体（议论文、记叙文、散文）、
   目标字数（800/1000）、立意角度数量、是否开启「审题→提纲→正文→自评→润色」全流程，
   点击生成后**流式**显示各个阶段的过程与正文。
2. **多阶段提示词管线**：后端按 审题立意 → 结构提纲 → 正文成篇 → 评分自检 → 润色定稿
   依次调用大模型，每个阶段的输出通过 SSE 实时推给前端（含阶段开始/结束事件）。
3. **范文库检索（RAG-lite）**：本地已抽取 20 份高考满分作文语料 `data/corpus.json`，
   提供按关键词打分检索，返回可用的论据/名言/句式片段，前端可一键“插入参考素材”。
4. **评分报告**：对最终作文给出「基础等级 / 发展等级 / 总分 / 亮点 / 硬伤 / 修改建议」，
   以结构化 JSON + 可视化条形图展示。
5. **多用户系统**：注册 / 登录（用户名 + 密码，token 认证），每个用户有独立的历史记录与收藏。
6. **历史记录 + 收藏 + 导出**：列表、查看详情、收藏/取消、导出 Markdown 与 DOCX 下载。
7. **首页信息**：页面显著位置显示「班级 / 姓名 / 学号」，数据来自后端 `GET /api/meta`。
8. **提示词查看页**：把后端真正的系统提示词展示出来（用于实验报告“核心提示词”截图）。

## 2. 技术栈与依赖（严格限定）

- Python 3.11+，`fastapi`，`uvicorn[standard]`，`openai`（1.x，仅用 `AsyncOpenAI`），`python-dotenv`。
  数据库用标准库 `sqlite3`；密码哈希用标准库 `hashlib`；token 用标准库 `hmac`+`base64`。
  **不要**引入 sqlalchemy / passlib / python-jose / pydantic-settings / jinja2 等。
- 前端：纯 HTML + CSS + 原生 JS（ES2020），**无构建、无 CDN、无外部字体**，
  所有样式写在一个 `static/style.css`，脚本写在一个 `static/app.js`。
- 页面中文 UI，风格：深色学术风，主色 `#1f6feb`/`#0d1117`，衬线标题（`Songti SC`/`Noto Serif SC`/`serif`），
  卡片圆角 12px，留白充足，移动端可用（窄屏单列）。

## 3. HTTP 接口契约（必须逐字实现）

统一前缀 `/api`。认证：`Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/meta` | 返回 `{app_title, student_class, student_name, student_id, model, corpus_count}` |
| GET | `/api/prompts` | 返回 `{stages: [{key, title, system, user_template}], strategies: [{name, desc}]}` |
| POST | `/api/auth/register` | body `{username, password, nickname?}` → `{token, user:{id,username,nickname,created_at}}`；用户名重复返回 400 |
| POST | `/api/auth/login` | 同上 body → `{token, user}`；失败 401 |
| GET | `/api/me` | 需认证 → `{user}` |
| POST | `/api/generate` | 需认证；body `{topic, material?, requirement?, genre, word_count, angle_count, do_critique}` → **SSE** |
| POST | `/api/corpus/search` | body `{query, top_k?}` → `{results:[{doc_title, text, score}]}`（无需认证） |
| GET | `/api/corpus/stats` | → `{count, docs:[{title, chars}]}` |
| GET | `/api/essays` | 需认证；query `limit`（默认 20）→ `{items:[{id,title,topic,genre,word_count,score,favorite,created_at}]}` |
| GET | `/api/essays/{id}` | 需认证，只能看自己的 → 完整记录 `{id,title,topic,genre,content,outline,critique,score_json,created_at,favorite}` |
| POST | `/api/essays/{id}/favorite` | body `{favorite: bool}` → `{ok:true}` |
| DELETE | `/api/essays/{id}` | → `{ok:true}` |
| GET | `/api/essays/{id}/export?fmt=md\|docx` | 返回文件下载（`Content-Disposition: attachment`） |
| GET | `/api/health` | → `{status:"ok", model}` |

### SSE 事件格式（`text/event-stream`）

每条事件为 `data: {json}\n\n`，json 的 `type` 取值：

- `{"type":"stage","stage":"outline","status":"start","title":"一、审题立意"}`
- `{"type":"delta","stage":"outline","text":"..."}`（流式增量）
- `{"type":"stage","stage":"outline","status":"done","content":"..."}`
- `{"type":"meta","title":"作文标题","outline":"...","critique":"...","score":{...}}`（最终，含评分）
- `{"type":"saved","essay_id":12}`（写库成功后）
- `{"type":"error","message":"..."}`
- `{"type":"done"}`（最后一条）

流结束后必须关闭。前端断开（`await request.is_disconnected()`）时应停止继续请求模型。

## 4. 数据与文件

- `data/corpus.json`（已存在）：`{"docs":[{"title":"...","text":"..."}], "count":N}`。
  检索用朴素 BM25（k1=1.5, b=0.75），中文按「单字 + 双字」切分做词项，无需分词库。
- `data/app.db`（运行时生成，必须列入 .gitignore）：表
  - `users(id INTEGER PK, username TEXT UNIQUE, password_hash TEXT, salt TEXT, nickname TEXT, created_at TEXT)`
  - `essays(id INTEGER PK, user_id INTEGER, title TEXT, topic TEXT, genre TEXT, content TEXT, outline TEXT, critique TEXT, score_json TEXT, word_count INTEGER, favorite INTEGER DEFAULT 0, created_at TEXT)`
- 导出 docx：用标准库 `zipfile` 手写最小 OOXML（`[Content_Types].xml`、`_rels/.rels`、`word/document.xml`、
  `word/_rels/document.xml.rels`），中文按 UTF-8 写入，段落用 `<w:p>`。不要为此引入 python-docx。

## 5. 已存在，禁止修改（只读引用）

| 文件 | 作用 |
|---|---|
| `app/config.py` | `settings` 单例：`openai_api_key / openai_base_url / model / model_fast / student_class / student_name / student_id / app_title / db_path / data_dir` |
| `app/schemas.py` | pydantic 模型：`GenerateRequest{model_config, topic, material, requirement, genre, word_count, angle_count, do_critique}`、`UserOut`、`EssaySummary`、`EssayDetail`、`SearchRequest`、`AuthRequest` |
| `app/llm.py` | `async def stream_chat(messages, model=None, temperature=0.8, max_tokens=2000) -> AsyncIterator[str]`；`async def complete_chat(messages, model=None, temperature=0.6, max_tokens=1200) -> str`；异常统一抛 `LLMError` |
| `app/prompts.py` | `GENRES: dict[str,str]`、`STAGE_TITLES: dict[str,str]`、`PROMPT_SPECS: list[dict]`（给 `/api/prompts`）、`STRATEGIES: list[dict]`、`build_*(req) -> list[dict]` 系列（见文件内签名） |
| `app/pipeline.py` | `async def run_pipeline(req: GenerateRequest) -> AsyncIterator[dict]`（直接产出上面 SSE 的 dict 事件，含 `meta`，不含 `saved`/`done`） |
| `data/corpus.json` | 范文语料 |
| `scripts/build_corpus.py` | 语料构建脚本 |

## 6. 你需要创建的文件

```
app/__init__.py
app/db.py            # sqlite3 连接池（每请求新建连接即可）、init_db()、User/Essay CRUD
app/auth.py          # hash_password/verify_password、sign_token/verify_token、get_current_user 依赖
app/corpus.py        # load_corpus()、bm25_search(query, top_k)
app/docx_export.py   # essay_to_docx_bytes(title, meta, content) -> bytes
app/main.py          # FastAPI 实例、静态挂载、全部路由、SSE 端点
static/index.html
static/style.css
static/app.js
requirements.txt
.env.example
.gitignore
```

### `app/main.py` 要求

- 启动时 `init_db()`；`app.mount("/static", StaticFiles(...))`；`GET /` 返回 `static/index.html`（`FileResponse`）。
- CORS 允许 `*`（便于本地用别的端口调试前端）。
- SSE：`StreamingResponse(run_pipeline(req), media_type="text/event-stream")`，
  并给每个 `data:` 之间加 `\n\n`；设置响应头 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。
  生成结束后把 title/outline/critique/content/score 落库（`type":"saved"` 事件里附 id）。
- 未认证访问 `/api/generate` 返回 401 JSON `{"detail":"未登录"}`。
- 全局异常处理：返回 `{"detail": str(exc)}`，不要把 traceback 吐给前端。

### 前端 `static/app.js` 要求

- 单页三视图切换（不刷新）：`工作台` / `历史记录` / `提示词`，顶部导航 + 右侧用户区。
- 工作台：左侧表单（题目 textarea、材料 textarea、写作要求 input、文体 select、字数 select、
  角度数 range 1-3、是否自评 checkbox、“检索范文素材”按钮 + 结果抽屉、“开始生成”按钮）；
  右侧实时面板：阶段时间轴（每阶段一个小圆点 + 标题 + 收起的输出）、正文打字机式流式渲染、
  评分卡片（总分大数字 + 基础/发展等级条形图 + 亮点/硬伤/建议列表）。
- SSE 用 `fetch` + `ReadableStream` 手动解析（不要用 EventSource，因为要 POST + 认证头），
  正确处理跨 chunk 的半条 `data:` 行。
- 未登录时弹出登录/注册浮层；token 存 `localStorage`。
- 历史记录：列表 → 点击查看详情（含正文、提纲、评分）→ 收藏 / 导出 md / 导出 docx。
- 提示词页：从 `/api/prompts` 拉取，按阶段折叠展示 `system` 与 `user_template`（等宽字体、可复制）。
- 首页页眉必须有醒目的「班级 / 姓名 / 学号」三枚 pill 标签，以及模型名、范文库条数。

## 7. 验收自检（你必须执行并把结果写进 `SELFCHECK.md`）

1. `python -m compileall app` 通过。
2. 用 `uvicorn app.main:app --port 8123` 启动（后台），`curl http://127.0.0.1:8123/api/health` 返回 ok。
3. 注册一个用户 → `/api/generate` 能收到至少 `stage/start`、`delta`、`meta`、`saved`、`done` 事件
   （真实调用模型，允许 30-120 秒）。
4. 历史记录能查到刚生成的作文；`/api/essays/{id}/export?fmt=docx` 下载的文件用 zipfile 能打开且含正文片段。
5. `/api/corpus/search` 对 `"坚持 理想"` 返回非空。
6. 把上述命令与输出摘要（每条 3 行内）写进 `SELFCHECK.md`。

注意：Windows PowerShell 环境，Python 解释器用项目内 `.venv\Scripts\python.exe`（若不存在则用 `python`）。
不要试图 `pip install` 新依赖；不要修改 `requirements.txt` 之外的依赖清单。
