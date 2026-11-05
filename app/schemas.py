"""请求/响应模型（pydantic v2）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32, description="登录名，2-32 字符")
    password: str = Field(min_length=6, max_length=64, description="密码，至少 6 位")
    nickname: str | None = Field(default=None, max_length=32)


class UserOut(BaseModel):
    id: int
    username: str
    nickname: str | None = None
    created_at: str


class AuthResponse(BaseModel):
    token: str
    user: UserOut


class GenerateRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=500, description="作文题目")
    material: str = Field(default="", max_length=4000, description="材料/引言/情境")
    requirement: str = Field(default="", max_length=1000, description="写作要求，如“自选角度，不少于800字”")
    genre: str = Field(default="议论文", description="议论文/记叙文/散文")
    word_count: int = Field(default=800, ge=600, le=1500)
    angle_count: int = Field(default=3, ge=1, le=3, description="候选立意数量")
    do_critique: bool = Field(default=True, description="是否执行评分自检与润色")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=120)
    top_k: int = Field(default=4, ge=1, le=10)


class FavoriteRequest(BaseModel):
    favorite: bool = True


class EssaySummary(BaseModel):
    id: int
    title: str
    topic: str
    genre: str
    word_count: int
    score: float | None = None
    favorite: bool = False
    created_at: str


class EssayDetail(EssaySummary):
    content: str
    outline: str = ""
    critique: str = ""
    score_json: dict[str, Any] | None = None


__all__ = [
    "AuthRequest",
    "UserOut",
    "AuthResponse",
    "GenerateRequest",
    "SearchRequest",
    "FavoriteRequest",
    "EssaySummary",
    "EssayDetail",
]
