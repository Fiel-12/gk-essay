"""全局配置：从 .env 读取，缺失时给出可用默认值。

设计原则：任何配置项都有默认值，缺 .env 也能启动（只是模型调用会失败并给出明确提示）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def _load_env(path: Path) -> None:
    """优先用 python-dotenv；没装则退化为极简解析，保证项目可启动。"""
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(path, override=False)
        return
    except Exception:  # pragma: no cover - 仅在缺依赖时走到
        pass
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env(ROOT / ".env")


def _env(key: str, default: str) -> str:
    value = os.environ.get(key, default)
    return value.strip() if isinstance(value, str) else default


@dataclass(frozen=True)
class Settings:
    # ---- 大模型 ----
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.deepseek.com/v1"))
    # 主模型负责成篇，快模型负责审题/评分这类结构化任务
    model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "deepseek-chat"))
    model_fast: str = field(default_factory=lambda: _env("OPENAI_MODEL_FAST", ""))
    request_timeout: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT", "180")))

    # ---- 站点信息（README/首页要求展示班级姓名学号）----
    app_title: str = field(default_factory=lambda: _env("APP_TITLE", "高考满分作文智能应用"))
    student_class: str = field(default_factory=lambda: _env("STUDENT_CLASS", "网安243"))
    student_name: str = field(default_factory=lambda: _env("STUDENT_NAME", "刘经治"))
    student_id: str = field(default_factory=lambda: _env("STUDENT_ID", "202401050896"))

    # ---- 存储 ----
    db_path: str = field(default_factory=lambda: _env("DB_PATH", str(DATA_DIR / "app.db")))
    corpus_path: str = field(default_factory=lambda: _env("CORPUS_PATH", str(DATA_DIR / "corpus.json")))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", "dev-only-change-me-in-env"))

    @property
    def fast_model(self) -> str:
        return self.model_fast or self.model

    @property
    def configured(self) -> bool:
        return bool(self.openai_api_key)


settings = Settings()

__all__ = ["settings", "ROOT", "DATA_DIR"]
