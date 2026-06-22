"""Play history API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from database import db

router = APIRouter()


class PlayProgress(BaseModel):
    """Play progress update request."""
    media_id: int
    episode_id: int
    progress_seconds: int
    duration_seconds: int = None
    completed: bool = False


@router.post("/history/update")
async def update_play_progress(progress: PlayProgress):
    """Update play progress for an episode."""
    async with db.connect() as conn:
        # Check if episode exists
        episode = await conn.execute(
            "SELECT id FROM episodes WHERE id = ?",
            [progress.episode_id]
        )
        if not await episode.fetchone():
            raise HTTPException(status_code=404, detail="Episode not found")

        # Upsert play history
        existing = await conn.execute(
            "SELECT id FROM play_history WHERE episode_id = ?",
            [progress.episode_id]
        )
        existing_row = await existing.fetchone()

        if existing_row:
            await conn.execute(
                """UPDATE play_history
                   SET progress_seconds = ?,
                       duration_seconds = ?,
                       completed = ?,
                       last_played_at = CURRENT_TIMESTAMP
                   WHERE episode_id = ?""",
                [
                    progress.progress_seconds,
                    progress.duration_seconds,
                    progress.completed,
                    progress.episode_id
                ]
            )
        else:
            await conn.execute(
                """INSERT INTO play_history
                   (media_id, episode_id, progress_seconds, duration_seconds, completed)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    progress.media_id,
                    progress.episode_id,
                    progress.progress_seconds,
                    progress.duration_seconds,
                    progress.completed
                ]
            )

        await conn.commit()

    return {"status": "ok"}


@router.get("/history/{media_id}")
async def get_play_history(media_id: int):
    """Get play history for a media."""
    async with db.connect() as conn:
        rows = await conn.execute(
            """SELECT ph.*, e.episode_number
               FROM play_history ph
               JOIN episodes e ON ph.episode_id = e.id
               WHERE ph.media_id = ?
               ORDER BY ph.last_played_at DESC""",
            [media_id]
        )
        history = [dict(row) for row in await rows.fetchall()]

    return {"history": history}


@router.get("/history")
async def get_all_play_history(limit: int = 20):
    """Get recent play history across all media."""
    async with db.connect() as conn:
        rows = await conn.execute(
            """SELECT ph.*, m.title as media_title, e.episode_number
               FROM play_history ph
               JOIN media m ON ph.media_id = m.id
               JOIN episodes e ON ph.episode_id = e.id
               ORDER BY ph.last_played_at DESC
               LIMIT ?""",
            [limit]
        )
        history = [dict(row) for row in await rows.fetchall()]

    return {"history": history}


@router.get("/history/{media_id}/continue")
async def get_continue_watching(media_id: int):
    """Get the episode to continue watching for a media."""
    async with db.connect() as conn:
        # Find the most recent episode that wasn't completed
        row = await conn.execute(
            """SELECT ph.*, e.episode_number
               FROM play_history ph
               JOIN episodes e ON ph.episode_id = e.id
               WHERE ph.media_id = ? AND ph.completed = 0
               ORDER BY ph.last_played_at DESC
               LIMIT 1""",
            [media_id]
        )
        result = await row.fetchone()

        if result:
            result_dict = dict(result)
            # Calculate progress percentage
            if result_dict.get("duration_seconds"):
                result_dict["progress_percent"] = (
                    result_dict["progress_seconds"] / result_dict["duration_seconds"] * 100
                )
            else:
                result_dict["progress_percent"] = 0
            return result_dict

        # If no incomplete episodes, return the last played episode
        row = await conn.execute(
            """SELECT ph.*, e.episode_number
               FROM play_history ph
               JOIN episodes e ON ph.episode_id = e.id
               WHERE ph.media_id = ?
               ORDER BY ph.last_played_at DESC
               LIMIT 1""",
            [media_id]
        )
        result = await row.fetchone()

        if result:
            return dict(result)

        return None
