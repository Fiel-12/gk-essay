"""FastAPI 入口：静态资源、全部 /api 路由与 SSE 生成端点。

要点：
- SSE 端点需要 POST + Authorization 头，前端只能用 fetch+ReadableStream，不能用 EventSource。
- 生成流结束前把 meta 落库并补发 saved 事件，再以 done 收尾；前端断开时及时终止，
  避免用户关掉页面后后端还在烧模型 token。
- 全局异常统一折叠成 {"detail": ...}，绝不把 traceback 泄给前端。
"""
from __future__ import annotations

import json
import re
from typing import AsyncIterator
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db, prompts
from .auth import get_current_user, hash_password, sign_token, verify_password
from .config import ROOT, settings
from .corpus import bm25_search, load_corpus
from .docx_export import essay_to_docx_bytes
from .pipeline import run_pipeline
from .schemas import AuthRequest, FavoriteRequest, GenerateRequest, SearchRequest, UserOut

app = FastAPI(title=settings.app_title)

# 本地用别的端口调试前端时不会撞到跨域限制；项目用 Bearer token 认证，无需 credentials
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

# 启动即建表（幂等），保证首次运行也能直接注册、生成
db.init_db()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _sse(event: dict) -> str:
    """把一条事件编码成 SSE 帧；ensure_ascii=False 让中文直接可读。"""
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def _essay_to_detail(row: dict) -> dict:
    score_json: dict | None = None
    if row.get("score_json"):
        try:
            parsed = json.loads(row["score_json"])
            if isinstance(parsed, dict):
                score_json = parsed
        except json.JSONDecodeError:
            score_json = None
    total = score_json.get("total") if score_json else None
    return {
        "id": row["id"],
        "title": row["title"],
        "topic": row["topic"],
        "genre": row["genre"],
        "word_count": row["word_count"],
        "score": float(total) if total is not None else None,
        "favorite": bool(row["favorite"]),
        "created_at": row["created_at"],
        "content": row["content"],
        "outline": row["outline"] or "",
        "critique": row["critique"] or "",
        "score_json": score_json,
    }


def _safe_filename(name: str) -> str:
    """去掉文件名里 Windows 不允许的字符，避免 Content-Disposition 下载失败。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip(" ._")
    return cleaned or "作文"


def _essay_to_markdown(row: dict, score_json: dict | None) -> str:
    lines = [f"# {row['title']}", ""]
    lines.append(f"- 题目：{row['topic']}")
    lines.append(f"- 文体：{row['genre']}")
    lines.append(f"- 字数：{row['word_count']}")
    lines.append(f"- 生成时间：{row['created_at']}")
    if score_json:
        lines.append(f"- 评分：{score_json.get('total', '—')}（{score_json.get('verdict', '')}）")
    lines += ["", "## 正文", "", row["content"], ""]
    if row.get("outline"):
        lines += ["## 结构提纲", "", row["outline"], ""]
    if row.get("critique"):
        lines += ["## 评分自检", "", row["critique"], ""]
    return "\n".join(lines)


async def _sse_generate(req: GenerateRequest, user_id: int, request: Request) -> AsyncIterator[str]:
    """驱动 run_pipeline 并转成 SSE；meta 事件到达时写库并补发 saved。"""
    try:
        async for event in run_pipeline(req):
            # 用户已断开（关标签页/断网）就立刻停，不再继续请求模型
            if await request.is_disconnected():
                return
            if event.get("type") == "meta":
                score = event.get("score") or {}
                essay_id = db.create_essay(
                    user_id=user_id,
                    title=str(event.get("title") or req.topic),
                    topic=req.topic,
                    genre=req.genre,
                    content=str(event.get("content") or ""),
                    outline=str(event.get("outline") or ""),
                    critique=str(event.get("critique") or ""),
                    score_json=json.dumps(score, ensure_ascii=False) if score else "",
                    word_count=int(event.get("word_count") or 0),
                )
                yield _sse(event)
                yield _sse({"type": "saved", "essay_id": essay_id})
            else:
                yield _sse(event)
        yield _sse({"type": "done"})
    except Exception as exc:  # noqa: BLE001 - SSE 流内异常也要以事件形式告知前端
        yield _sse({"type": "error", "message": str(exc)})


# ---------------------------------------------------------------------------
# 站点信息 / 健康检查 / 提示词
# ---------------------------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(ROOT / "static" / "index.html"))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "model": settings.model}


@app.get("/api/meta")
def meta() -> dict:
    docs = load_corpus()
    return {
        "app_title": settings.app_title,
        "student_class": settings.student_class,
        "student_name": settings.student_name,
        "student_id": settings.student_id,
        "model": settings.model,
        "corpus_count": len(docs),
    }


@app.get("/api/prompts")
def get_prompts() -> dict:
    return {"stages": prompts.PROMPT_SPECS, "strategies": prompts.STRATEGIES}


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------

@app.post("/api/auth/register")
def register(body: AuthRequest) -> dict:
    if db.get_user_by_username(body.username):
        raise HTTPException(status_code=400, detail="用户名已存在")
    digest, salt = hash_password(body.password)
    user = db.create_user(body.username, digest, salt, body.nickname or body.username)
    token = sign_token(user["id"])
    return {
        "token": token,
        "user": UserOut(
            id=user["id"],
            username=user["username"],
            nickname=user.get("nickname") or None,
            created_at=user["created_at"],
        ).model_dump(),
    }


@app.post("/api/auth/login")
def login(body: AuthRequest) -> dict:
    user = db.get_user_by_username(body.username)
    if not user or not verify_password(body.password, user["salt"], user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = sign_token(user["id"])
    return {
        "token": token,
        "user": UserOut(
            id=user["id"],
            username=user["username"],
            nickname=user.get("nickname") or None,
            created_at=user["created_at"],
        ).model_dump(),
    }


@app.get("/api/me")
def me(user: UserOut = Depends(get_current_user)) -> dict:
    return {"user": user.model_dump()}


# ---------------------------------------------------------------------------
# 生成（SSE）
# ---------------------------------------------------------------------------

@app.post("/api/generate")
async def generate(
    req: GenerateRequest,
    request: Request,
    user: UserOut = Depends(get_current_user),
) -> StreamingResponse:
    return StreamingResponse(
        _sse_generate(req, user.id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 范文库检索
# ---------------------------------------------------------------------------

@app.post("/api/corpus/search")
def corpus_search(body: SearchRequest) -> dict:
    return {"results": bm25_search(body.query, body.top_k)}


@app.get("/api/corpus/stats")
def corpus_stats() -> dict:
    docs = load_corpus()
    return {
        "count": len(docs),
        "docs": [{"title": d["title"], "chars": len(d["text"])} for d in docs],
    }


# ---------------------------------------------------------------------------
# 历史记录 / 收藏 / 导出
# ---------------------------------------------------------------------------

@app.get("/api/essays")
def essays(limit: int = 20, user: UserOut = Depends(get_current_user)) -> dict:
    limit = max(1, min(limit, 100))
    return {"items": db.list_essays(user.id, limit)}


@app.get("/api/essays/{essay_id}")
def essay_detail(essay_id: int, user: UserOut = Depends(get_current_user)) -> dict:
    row = db.get_essay(essay_id)
    if not row or row["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="作文不存在")
    return _essay_to_detail(row)


@app.post("/api/essays/{essay_id}/favorite")
def favorite(essay_id: int, body: FavoriteRequest, user: UserOut = Depends(get_current_user)) -> dict:
    if not db.set_favorite(essay_id, user.id, body.favorite):
        raise HTTPException(status_code=404, detail="作文不存在")
    return {"ok": True}


@app.delete("/api/essays/{essay_id}")
def delete(essay_id: int, user: UserOut = Depends(get_current_user)) -> dict:
    if not db.delete_essay(essay_id, user.id):
        raise HTTPException(status_code=404, detail="作文不存在")
    return {"ok": True}


@app.get("/api/essays/{essay_id}/export")
def export(essay_id: int, fmt: str = "md", user: UserOut = Depends(get_current_user)) -> Response:
    row = db.get_essay(essay_id)
    if not row or row["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="作文不存在")
    score_json: dict | None = None
    if row.get("score_json"):
        try:
            parsed = json.loads(row["score_json"])
            if isinstance(parsed, dict):
                score_json = parsed
        except json.JSONDecodeError:
            score_json = None

    base_name = _safe_filename(row["title"])
    if fmt == "docx":
        meta_dict = {
            "topic": row["topic"],
            "genre": row["genre"],
            "word_count": row["word_count"],
            "created_at": row["created_at"],
            "outline": row.get("outline"),
            "critique": row.get("critique"),
            "score": score_json,
        }
        data = essay_to_docx_bytes(row["title"], meta_dict, row["content"])
        filename = f"{base_name}.docx"
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        data = _essay_to_markdown(row, score_json).encode("utf-8")
        filename = f"{base_name}.md"
        media_type = "text/markdown; charset=utf-8"

    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


# ---------------------------------------------------------------------------
# 全局兜底异常
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # 只给一句话错误信息，traceback 留在服务端日志里，不暴露实现细节
    return JSONResponse(status_code=500, content={"detail": str(exc)})


__all__ = ["app"]
