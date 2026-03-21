"""
api/routes/jury.py — эндпоинты для жюри и администраторов

GET  /api/jury/me       — моя статистика
GET  /api/jury/next     — взять следующий пост
POST /api/jury/submit   — сдать результат
POST /api/jury/skip     — пропустить пост
POST /api/jury/reject   — отклонить пост
GET  /api/jury/stats    — общая статистика конкурса
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_verified_user
from utils.db.jury import get_my_stats
from utils.db.posts import get_posts_stats, save_result, reject_post, errors_per_1000
from parser.queue_manager import (
    take_post, get_active_post, release_post,
    remove_post, get_total_queue_count,
)
from parser.posts import fetch_post_text
from utils.ai_utils import check_post
from utils.config import MAX_WORDS, MAX_ERRORS
from utils.logger import setup_logger

log = setup_logger()
router = APIRouter(prefix="/jury", tags=["jury"])


# ── Схемы ─────────────────────────────────────────────────────────────────────

class SubmitResult(BaseModel):
    words:  int = Field(..., ge=0, le=MAX_WORDS)
    errors: int = Field(..., ge=0, le=MAX_ERRORS)


class SkipPost(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class RejectPost(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.get("/me")
def get_my_stats_route(user: dict = Depends(get_verified_user)):
    """Статистика текущего жюри."""
    data = get_my_stats(user["tg_id"])
    if not data:
        raise HTTPException(status_code=404, detail="Reviewer not found")
    return {
        **data,
        "errors_per_1000": errors_per_1000(data["total_errors"], data["total_words"]),
    }


@router.get("/stats")
def get_full_stats(user: dict = Depends(get_verified_user)):
    """Общая статистика конкурса."""
    stats = get_posts_stats()
    return {
        **stats,
        "in_queue": get_total_queue_count(),
    }


@router.get("/active")
def get_active_post_route(user: dict = Depends(get_verified_user)):
    """Возвращает текущий активный пост если есть, иначе 404."""
    post = get_active_post(user["tg_id"])
    if not post:
        raise HTTPException(status_code=404, detail="No active post")
    return {
        "post_id":   post["post_id"],
        "url":       post["url"],
        "author":    post["author"],
        "bot_words": post["bot_words"],
    }


@router.get("/next")
def get_next_post(user: dict = Depends(get_verified_user)):
    """
    Взять следующий пост для проверки.
    Если уже есть активный — возвращает его.
    """
    tg_id = user["tg_id"]

    post = get_active_post(tg_id)
    if post:
        log.info(f"[api/next] {tg_id} уже имеет активный пост #{post['post_id']}")
    else:
        post = take_post(tg_id)
        if not post:
            raise HTTPException(status_code=404, detail="No posts available")
        log.info(f"[api/next] {tg_id} взял пост #{post['post_id']}")

    return {
        "post_id":   post["post_id"],
        "url":       post["url"],
        "author":    post["author"],
        "bot_words": post["bot_words"],
    }


@router.post("/submit")
def submit_result(body: SubmitResult, user: dict = Depends(get_verified_user)):
    """Сохранить результат проверки поста."""
    tg_id = user["tg_id"]

    post = get_active_post(tg_id)
    if not post:
        raise HTTPException(status_code=400, detail="No active post")

    success = save_result(post["post_id"], tg_id, body.words, body.errors)
    if not success:
        raise HTTPException(status_code=409, detail="Failed to save result")

    remove_post(post["post_id"])
    log.info(f"[api/submit] {tg_id} → #{post['post_id']}: слов={body.words}, ошибок={body.errors}")
    return {"ok": True}


@router.post("/skip")
def skip_post(body: SkipPost, user: dict = Depends(get_verified_user)):
    """Пропустить текущий пост (возвращается в очередь)."""
    tg_id = user["tg_id"]

    post = get_active_post(tg_id)
    if not post:
        raise HTTPException(status_code=400, detail="No active post")

    release_post(tg_id, post["post_id"])
    log.info(f"[api/skip] {tg_id} → #{post['post_id']}: {body.reason}")
    return {"ok": True}


@router.post("/reject")
def reject_post_route(body: RejectPost, user: dict = Depends(get_verified_user)):
    """Отклонить текущий пост."""
    tg_id = user["tg_id"]

    post = get_active_post(tg_id)
    if not post:
        raise HTTPException(status_code=400, detail="No active post")

    success = reject_post(post["post_id"], tg_id, body.reason)
    if not success:
        raise HTTPException(status_code=409, detail="Failed to reject post")

    remove_post(post["post_id"])
    log.info(f"[api/reject] {tg_id} → #{post['post_id']}: {body.reason}")
    return {"ok": True}


@router.get("/post-text")
def get_post_text(user: dict = Depends(get_verified_user)):
    """Возвращает текст текущего активного поста (парсинг на лету)."""
    tg_id = user["tg_id"]
    post = get_active_post(tg_id)
    if not post:
        raise HTTPException(status_code=400, detail="No active post")
    text = fetch_post_text(post["url"])
    return {"text": text or ""}


@router.get("/ai-check")
def ai_check_post(user: dict = Depends(get_verified_user)):
    """Запустить AI-проверку текущего поста."""
    tg_id = user["tg_id"]

    post = get_active_post(tg_id)
    if not post:
        raise HTTPException(status_code=400, detail="No active post")

    text = fetch_post_text(post["url"])
    if not text:
        raise HTTPException(status_code=502, detail="Failed to fetch post text")

    results = check_post(text)
    return {"results": results}
