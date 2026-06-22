"""Video playback API endpoints."""

import os
import mimetypes
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from config import settings
from database import db

router = APIRouter()


@router.get("/stream/{media_id}/{episode_id}")
async def stream_video(request: Request, media_id: int, episode_id: int):
    """Stream a video file with HTTP Range support."""
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

    # Get file info
    file_size = file_path.stat().st_size
    mime_type, _ = mimetypes.guess_type(str(file_path))
    mime_type = mime_type or "video/mp4"

    # Handle Range requests
    range_header = request.headers.get("range")

    if range_header:
        range_match = range_header.strip().lower().startswith("bytes=")
        if range_match:
            range_str = range_header.strip()[6:]
            parts = range_str.split("-")

            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] and parts[1] != "" else file_size - 1

            if start >= file_size:
                raise HTTPException(
                    status_code=416,
                    detail=f"Requested range not satisfiable: {start}-{end} / {file_size}"
                )

            length = end - start + 1
            content_range = f"bytes {start}-{end}/{file_size}"

            def iterfile():
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    chunk_size = 1024 * 1024  # 1MB chunks
                    while remaining > 0:
                        to_read = min(chunk_size, remaining)
                        data = f.read(to_read)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return StreamingResponse(
                iterfile(),
                status_code=206,
                headers={
                    "Content-Range": content_range,
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                    "Content-Type": mime_type,
                },
                media_type=mime_type,
            )

    # Full file response
    def iterfile():
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        iterfile(),
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": mime_type,
        },
        media_type=mime_type,
    )


@router.get("/video/{media_id}/{episode_id}")
async def get_video_info(media_id: int, episode_id: int):
    """Get video file information."""
    async with db.connect() as conn:
        episode = await conn.execute(
            "SELECT * FROM episodes WHERE id = ?",
            [episode_id]
        )
        episode = await episode.fetchone()

        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

    return {
        "id": episode["id"],
        "episode_number": episode["episode_number"],
        "title": episode["title"],
        "file_path": episode["file_path"],
        "file_size": episode["file_size"],
        "format": episode["format"],
        "resolution": episode["resolution"],
    }
