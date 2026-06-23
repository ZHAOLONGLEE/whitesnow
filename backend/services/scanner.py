"""
Media scanner service — scans NAS directory and populates database.
"""

import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from pypinyin import pinyin, Style
from config import settings
from database import db
from services.metadata import MetadataScraper


# Common video extensions
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts"
}

# Common cover image extensions
COVER_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp"
}

# QNAP system folders (e.g. @Recycle, @Recently-Snapshot) are never real categories
SYSTEM_FOLDER_PREFIXES = ("@", ".")

# Common cover file names
COVER_NAMES = {
    "poster", "cover", "folder", "thumb", "thumbnail",
    "fanart", "banner", "landscape", "portrait"
}

# S01E01 style season+episode marker
SEASON_EPISODE_PATTERN = re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})')

# Single-episode-number markers: 第01集 / EP01 / E01 / 12期
EPISODE_NUMBER_PATTERN = re.compile(
    r'(?:第|EP?|集|話|话)\s*(\d{1,3})(?!\d)\s*(?:集|話|话)?|(\d{1,3})(?!\d)\s*期',
    re.IGNORECASE
)

# 第N季 inside a folder name
SEASON_FOLDER_PATTERN = re.compile(r'第\s*(\d+)\s*季')

# Technical release-tag markers parsed from a video filename
RESOLUTION_PATTERN = re.compile(r'(4320p|8K|2160p|4K|1080p|720p|480p)', re.IGNORECASE)
VIDEO_CODEC_PATTERN = re.compile(r'(H\.?265|HEVC|x265|H\.?264|AVC|x264)', re.IGNORECASE)
AUDIO_CODEC_PATTERN = re.compile(r'(AAC|DDP|EAC3|AC3|DTS|FLAC)', re.IGNORECASE)
MEDIA_SOURCE_PATTERN = re.compile(r'(WEB-?DL|WEBRip|BluRay|BDRip|HDTV|DVDRip)', re.IGNORECASE)


class MediaScanner:
    """Scan media directories and populate database."""

    def __init__(self):
        self.media_root = Path(settings.media_root)
        self.items_scanned = 0
        self.items_added = 0
        # Cover storage path
        self.cover_storage = Path(settings.cover_storage)
        self.cover_storage.mkdir(parents=True, exist_ok=True)
        self.scraper = MetadataScraper()
        self.exclude_folders = {
            name.strip() for name in settings.media_exclude_folders.split(",") if name.strip()
        }

    async def scan(self, on_progress=None) -> Dict:
        """Main scan entry point.

        on_progress, if given, is awaited as on_progress(items_scanned,
        items_added, total) after every show folder is processed, so a
        caller can report live progress for large libraries.
        """
        self.items_scanned = 0
        self.items_added = 0

        if not self.media_root.exists():
            raise FileNotFoundError(f"Media root not found: {self.media_root}")

        show_folders = []
        for category_folder in sorted(self.media_root.iterdir()):
            if not category_folder.is_dir():
                continue
            if category_folder.name.startswith(SYSTEM_FOLDER_PREFIXES):
                continue
            if category_folder.name in self.exclude_folders:
                continue
            type_name = category_folder.name

            for show_folder in sorted(category_folder.iterdir()):
                if show_folder.is_dir():
                    show_folders.append((type_name, show_folder))

        total = len(show_folders)
        for type_name, show_folder in show_folders:
            await self._process_show(type_name, show_folder)
            if on_progress:
                await on_progress(self.items_scanned, self.items_added, total)

        return {
            "items_scanned": self.items_scanned,
            "items_added": self.items_added,
            "total": total
        }

    async def _process_show(self, type_name: str, show_folder: Path):
        """Scan a single show folder (category/show), recursing for episodes."""
        video_files = self._find_video_files(show_folder)
        if not video_files:
            return

        title = show_folder.name
        clean_title = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]', '', title).lower()
        title_pinyin = self._generate_pinyin(title)
        year_match = re.search(r'(20\d{2})', title)
        year = int(year_match.group(1)) if year_match else None
        tech_info = self._parse_tech_info(video_files[0].name)

        local_cover = self._find_cover_image(show_folder, clean_title)
        meta_data = await self.scraper.scrape(title)

        online_cover = None
        if meta_data.get("cover_url"):
            online_cover = await self.scraper.download_and_save_cover(
                meta_data["cover_url"], clean_title
            )

        final_cover = local_cover or online_cover
        final_description = meta_data.get("description")
        final_year = meta_data.get("year") or year
        final_rating = meta_data.get("rating")

        async with db.connect() as conn:
            existing = await conn.execute(
                "SELECT id FROM media WHERE title_clean = ?",
                [clean_title]
            )
            existing_row = await existing.fetchone()

            if existing_row:
                media_id = existing_row["id"]
                await conn.execute("DELETE FROM episodes WHERE media_id = ?", [media_id])
            else:
                result = await conn.execute(
                    """INSERT INTO media (
                        title, title_original, title_clean, title_pinyin, type, category,
                        year, total_episodes, cover_path, description, rating,
                        resolution, video_codec, audio_codec, media_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        # category is no longer populated — superseded by type (the
                        # literal NAS category-folder name); column kept for schema compat.
                        title, show_folder.name, clean_title, title_pinyin, type_name, None,
                        final_year, 0, final_cover, final_description, final_rating,
                        tech_info["resolution"], tech_info["video_codec"],
                        tech_info["audio_codec"], tech_info["media_source"]
                    ]
                )
                await conn.commit()
                media_id = result.lastrowid
                self.items_added += 1

            self.items_scanned += 1

            season_counters: Dict[int, int] = {}
            for video_file in video_files:
                season, episode_number, ep_title = self._parse_season_episode(
                    video_file, show_folder, season_counters
                )
                file_size = video_file.stat().st_size

                await conn.execute(
                    """INSERT INTO episodes (
                        media_id, season, episode_number, title, file_path,
                        file_size, format
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [
                        media_id, season, episode_number, ep_title, str(video_file),
                        file_size, video_file.suffix.lstrip(".").upper()
                    ]
                )
                await conn.commit()

            await conn.execute(
                """UPDATE media
                   SET total_episodes = ?, type = ?, cover_path = ?, description = ?, rating = ?,
                       resolution = ?, video_codec = ?, audio_codec = ?, media_source = ?
                   WHERE id = ?""",
                [
                    len(video_files), type_name, final_cover, final_description, final_rating,
                    tech_info["resolution"], tech_info["video_codec"],
                    tech_info["audio_codec"], tech_info["media_source"], media_id
                ]
            )
            await conn.commit()

    def _find_cover_image(self, show_folder: Path, clean_title: str) -> Optional[str]:
        """Look for a cover image in the show folder root and its direct subfolders."""
        search_dirs = [show_folder] + [d for d in show_folder.iterdir() if d.is_dir()]

        for directory in search_dirs:
            for file in directory.iterdir():
                if file.is_file() and file.suffix.lower() in COVER_EXTENSIONS:
                    if file.stem.lower() in COVER_NAMES:
                        return self._copy_cover(file, clean_title)

        for file in show_folder.iterdir():
            if file.is_file() and file.suffix.lower() in COVER_EXTENSIONS:
                return self._copy_cover(file, clean_title)

        return None

    def _copy_cover(self, source: Path, title_key: str) -> Optional[str]:
        """Copy cover image to static directory and return URL path."""
        try:
            # Generate unique filename
            ext = source.suffix.lower()
            dest_filename = f"{title_key}{ext}"
            dest_path = self.cover_storage / dest_filename

            # Copy file
            shutil.copy2(source, dest_path)

            # Return URL path
            return f"/static/covers/{dest_filename}"
        except Exception as e:
            print(f"Failed to copy cover: {e}")
            return None

    def _generate_pinyin(self, text: str) -> str:
        """Generate pinyin from Chinese text for search."""
        try:
            # Get pinyin with tone marks removed
            py_list = pinyin(text, style=Style.NORMAL)
            # Join all syllables
            full_pinyin = ''.join([item[0] for item in py_list])
            # Also create spaced version
            spaced_pinyin = ' '.join([item[0] for item in py_list])
            # Return both formats concatenated
            return f"{full_pinyin} {spaced_pinyin}".lower()
        except Exception:
            return ""

    def _parse_tech_info(self, filename: str) -> Dict[str, Optional[str]]:
        """Parse resolution/video codec/audio codec/media source from a release filename."""
        info: Dict[str, Optional[str]] = {
            "resolution": None, "video_codec": None, "audio_codec": None, "media_source": None
        }

        match = RESOLUTION_PATTERN.search(filename)
        if match:
            token = match.group(1).upper()
            if token == "4K":
                info["resolution"] = "2160p"
            elif token == "8K":
                info["resolution"] = "4320p"
            else:
                info["resolution"] = token.lower()

        match = VIDEO_CODEC_PATTERN.search(filename)
        if match:
            token = match.group(1).upper().replace(".", "")
            info["video_codec"] = "H265" if token in ("H265", "HEVC", "X265") else "H264"

        match = AUDIO_CODEC_PATTERN.search(filename)
        if match:
            info["audio_codec"] = match.group(1).upper()

        match = MEDIA_SOURCE_PATTERN.search(filename)
        if match:
            info["media_source"] = match.group(1).upper().replace("WEBDL", "WEB-DL")

        return info

    def _infer_season_from_path(self, video_file: Path, show_folder: Path) -> int:
        """Look for a "第N季" marker in any folder between show_folder and the file.

        Assumes video_file is a descendant of show_folder.
        """
        for parent in video_file.relative_to(show_folder).parts[:-1]:
            match = SEASON_FOLDER_PATTERN.search(parent)
            if match:
                return int(match.group(1))
        return 1

    def _parse_season_episode(
        self, video_file: Path, show_folder: Path, season_counters: Dict[int, int]
    ) -> Tuple[int, int, str]:
        """Parse season + episode number from a video filename."""
        name = video_file.stem

        match = SEASON_EPISODE_PATTERN.search(name)
        if match:
            season = int(match.group(1))
            episode_number = int(match.group(2))
            title = name[:match.start()].strip(' ._-') or f"第 {episode_number} 集"
            return season, episode_number, title

        season = self._infer_season_from_path(video_file, show_folder)

        match = EPISODE_NUMBER_PATTERN.search(name)
        if match:
            episode_number = int(match.group(1) or match.group(2))
            title = name[:match.start()].strip(' ._-') or f"第 {episode_number} 集"
            return season, episode_number, title

        season_counters[season] = season_counters.get(season, 0) + 1
        return season, season_counters[season], name

    def _find_video_files(self, show_folder: Path) -> List[Path]:
        """Recursively find every video file under a show folder, any depth."""
        return sorted(
            p for p in show_folder.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        )
