"""
api/app.py — FastAPI приложение
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import pathlib

from api.routes.jury  import router as jury_router
from api.routes.admin import router as admin_router

app = FastAPI(
    title="inkstory-bot API",
    version="3.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# CORS — разрешаем все origins (нужно для trycloudflare + Telegram Mini App)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Роуты
app.include_router(jury_router,  prefix="/api")
app.include_router(admin_router, prefix="/api")

# Фронтенд — отдаём статику из папки frontend/
FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "3.0"}
