import aiosqlite
import sqlite3
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from config import settings


class Database:
    """Async SQLite database manager."""

    _connection: aiosqlite.Connection | None = None

    @asynccontextmanager
    async def connect(self):
        """Create a database connection."""
        # Ensure data directory exists
        db_path = settings.database_url.replace("sqlite:///", "")
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        conn = await aiosqlite.connect(
            db_path,
            timeout=30,
            isolation_level=None  # Autocommit mode for better concurrency
        )
        conn.row_factory = aiosqlite.Row
        # Enable WAL mode for better concurrent access
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            await conn.close()

    async def init_db(self):
        """Initialize database tables."""
        async with self.connect() as conn:
            # Media library table
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    title_original TEXT,
                    title_clean TEXT,
                    title_pinyin TEXT,
                    type TEXT NOT NULL,
                    category TEXT,
                    cover_path TEXT,
                    description TEXT,
                    year INTEGER,
                    total_episodes INTEGER,
                    rating REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id INTEGER NOT NULL,
                    episode_number INTEGER NOT NULL,
                    title TEXT,
                    file_path TEXT NOT NULL,
                    file_size INTEGER,
                    duration INTEGER,
                    format TEXT,
                    resolution TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS play_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id INTEGER NOT NULL,
                    episode_id INTEGER NOT NULL,
                    progress_seconds INTEGER DEFAULT 0,
                    duration_seconds INTEGER,
                    completed BOOLEAN DEFAULT 0,
                    last_played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE,
                    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS scan_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP,
                    status TEXT NOT NULL,
                    items_scanned INTEGER DEFAULT 0,
                    items_added INTEGER DEFAULT 0,
                    error_message TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_media_title ON media(title_clean);
                CREATE INDEX IF NOT EXISTS idx_media_pinyin ON media(title_pinyin);
                CREATE INDEX IF NOT EXISTS idx_episodes_media ON episodes(media_id);
                CREATE INDEX IF NOT EXISTS idx_episodes_number ON episodes(episode_number);
                CREATE INDEX IF NOT EXISTS idx_play_history_media ON play_history(media_id);
                CREATE INDEX IF NOT EXISTS idx_play_history_episode ON play_history(episode_id);
            """)

            # Migration: episodes.season (added for multi-season shows)
            try:
                await conn.execute(
                    "ALTER TABLE episodes ADD COLUMN season INTEGER NOT NULL DEFAULT 1"
                )
                await conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

            # Migration: media technical info (parsed from release filenames)
            for column in ("resolution", "video_codec", "audio_codec", "media_source"):
                try:
                    await conn.execute(f"ALTER TABLE media ADD COLUMN {column} TEXT")
                    await conn.commit()
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise

    async def close(self):
        """Close database connection."""
        pass  # aiosqlite connections are short-lived


db = Database()
