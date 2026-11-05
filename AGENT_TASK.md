# 你的任务（编码智能体执行单）

你是被调用的编码智能体。工作目录：`D:\aigc\gaokao-essay-agent`。

## 第一步：读文件

按顺序读：

1. `SPEC.md` —— 完整实现规格（接口契约、SSE 事件、数据表、前端要求、自检清单）。
2. `app/config.py`、`app/schemas.py`、`app/llm.py`、`app/prompts.py`、`app/pipeline.py`
   —— 这些**已经写好且禁止修改**，你必须按它们真实的函数签名去接线。
3. `data/corpus.json` 只看前 40 行，了解结构即可。

## 第二步：实现

实现 `SPEC.md` 第 6 节列出的文件（`app/db.py`、`app/auth.py`、`app/corpus.py`、
`app/docx_export.py`、`app/main.py`、`static/index.html`、`static/style.css`、`static/app.js`、
`requirements.txt`、`.env.example`、`.gitignore`）。

硬性约束：

- 依赖只用 `fastapi`、`uvicorn`、`openai`、`python-dotenv`，其它一律用标准库。
- 前端零构建、零 CDN，`static/app.js` 自己解析 `fetch` + `ReadableStream` 的 SSE。
- 不要修改上面第 2 步里列出的任何已存在文件，也不要动 `data/corpus.json`、`scripts/`。
- 代码要有中文注释解释“为什么”，不要写成注释复述代码。
- 页面必须美观：深色学术风、卡片式、有渐变与微动效，中文字体栈
  `"Noto Serif SC", "Songti SC", "Source Han Serif SC", serif` 用于标题。
- 首页顶部必须显示班级 / 姓名 / 学号（从 `/api/meta` 取）。

## 第三步：自检

环境：Windows PowerShell 下的 Python 虚拟环境 `.venv\Scripts\python.exe`（已存在）。
依赖可能正在安装，如果 `import fastapi` 失败，等 60 秒重试一次，仍失败则跳过运行类自检。

按 `SPEC.md` 第 7 节逐条自检，把命令与输出摘要写进 `SELFCHECK.md`。
启动服务请用：`& .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8123`，
自检完记得把后台进程停掉。

## 第四步：收尾

最后输出一段 20 行以内的中文总结：实现了哪些文件、每条自检的结论、已知遗留问题。
