"""
WhiteSnow — 家庭媒体中心
本地 NAS 动漫/短剧在线观看与下载服务
"""

import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import db
from routers import media, play, download, scan, history

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management."""
    # Startup
    await db.init_db()
    app.state.db = db  # Make db available to request handlers
    print(f"✅ WhiteSnow started in {settings.app_env} mode")
    print(f"📁 Media root: {settings.media_root}")
    yield
    # Shutdown
    await db.close()
    print("👋 WhiteSnow stopped")


app = FastAPI(
    title="WhiteSnow",
    description="家庭媒体中心 — 本地 NAS 动漫/短剧在线观看",
    version="0.1.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files and templates
project_root = Path(__file__).parent.parent
frontend_static = project_root / "frontend" / "static"
templates_dir = project_root / "frontend" / "templates"

# Docker fallback paths
if not frontend_static.exists():
    frontend_static = Path("/frontend/static")
if not templates_dir.exists():
    templates_dir = Path("/frontend/templates")

app.mount("/static", StaticFiles(directory=str(frontend_static)), name="static")

# Templates
templates = Jinja2Templates(directory=str(templates_dir))

# Routers
app.include_router(media.router, prefix="/api", tags=["media"])
app.include_router(play.router, prefix="/api", tags=["play"])
app.include_router(download.router, prefix="/api", tags=["download"])
app.include_router(scan.router, prefix="/api", tags=["scan"])
app.include_router(history.router, prefix="/api", tags=["history"])


# Page routes
@app.get("/")
async def index(request: Request):
    """Home page — media library poster wall."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/media/{media_id}")
async def media_detail(request: Request, media_id: int):
    """Media detail page."""
    return templates.TemplateResponse("detail.html", {
        "request": request,
        "media_id": media_id
    })


@app.get("/play/{media_id}/{episode_id}")
async def play_page(request: Request, media_id: int, episode_id: int):
    """Video playback page."""
    return templates.TemplateResponse("play.html", {
        "request": request,
        "media_id": media_id,
        "episode_id": episode_id
    })


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}
