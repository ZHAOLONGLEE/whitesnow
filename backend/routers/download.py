"""Download API endpoints."""

import os
import mimetypes
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from config import settings
from database import db

router = APIRouter()


@router.get("/download/{episode_id}")
async def download_episode(episode_id: int):
    """Download a video file."""
    async with db.connect() as conn:
        episode = await conn.execute(
            "SELECT * FROM episodes WHERE id = ?",
            [episode_id]
        )
        episode = await episode.fetchone()

        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        file_path = Path(episode["file_path"])

        # Security: ensure file is within media root
        if not str(file_path).startswith(settings.media_mount):
            raise HTTPException(status_code=403, detail="Access denied")

        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

    # Get filename for download
    filename = file_path.name
    mime_type, _ = mimetypes.guess_type(str(file_path))

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=mime_type or "application/octet-stream"
    )
