"""Media library API endpoints."""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from database import db

router = APIRouter()


@router.get("/media")
async def list_media(
    type: Optional[str] = Query(None, description="Filter by type: anime or drama"),
    search: Optional[str] = Query(None, description="Search by title or pinyin"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List media items with pagination and filters."""
    offset = (page - 1) * page_size

    where_clauses = []
    params = []

    if type:
        where_clauses.append("m.type = ?")
        params.append(type)

    if search:
        # Search in title_clean, title_pinyin, and display title
        search_term = f"%{search}%"
        where_clauses.append("(m.title_clean LIKE ? OR m.title_pinyin LIKE ? OR m.title LIKE ?)")
        params.extend([search_term, search_term, search_term])

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    async with db.connect() as conn:
        # Get total count
        count_row = await conn.execute(
            f"SELECT COUNT(*) as cnt FROM media m WHERE {where_sql}",
            params
        )
        total = (await count_row.fetchone())["cnt"]

        # Get media list
        rows = await conn.execute(
            f"""
            SELECT m.*,
                   (SELECT COUNT(*) FROM episodes e WHERE e.media_id = m.id) as episode_count
            FROM media m
            WHERE {where_sql}
            ORDER BY m.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset]
        )
        items = [dict(row) for row in await rows.fetchall()]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }


@router.get("/media/types")
async def list_types():
    """List distinct media types with counts, for dynamic filter UI."""
    async with db.connect() as conn:
        rows = await conn.execute(
            "SELECT type, COUNT(*) as count FROM media GROUP BY type ORDER BY type"
        )
        items = [dict(row) for row in await rows.fetchall()]
    return {"items": items}


@router.get("/media/{media_id}")
async def get_media(media_id: int):
    """Get media detail with episode list."""
    async with db.connect() as conn:
        media_row = await conn.execute(
            "SELECT * FROM media WHERE id = ?",
            [media_id]
        )
        media = await media_row.fetchone()

        if not media:
            raise HTTPException(status_code=404, detail="Media not found")

        episodes_row = await conn.execute(
            "SELECT * FROM episodes WHERE media_id = ? ORDER BY season ASC, episode_number ASC",
            [media_id]
        )
        episodes = [dict(row) for row in await episodes_row.fetchall()]

        size_row = await conn.execute(
            "SELECT SUM(file_size) as total_size FROM episodes WHERE media_id = ?",
            [media_id]
        )
        total_size = (await size_row.fetchone())["total_size"]

    result = dict(media)
    result["episodes"] = episodes
    result["total_size"] = total_size
    return result


@router.get("/media/{media_id}/episodes")
async def list_episodes(media_id: int):
    """List episodes for a specific media."""
    async with db.connect() as conn:
        rows = await conn.execute(
            "SELECT * FROM episodes WHERE media_id = ? ORDER BY season ASC, episode_number ASC",
            [media_id]
        )
        episodes = [dict(row) for row in await rows.fetchall()]

    return {"episodes": episodes}
