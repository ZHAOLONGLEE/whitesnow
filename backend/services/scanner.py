"""
Media scanner service — scans NAS directory and populates database.
"""

import os
import re
import shutil
import asyncio
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

# Common cover file names
COVER_NAMES = {
    "poster", "cover", "folder", "thumb", "thumbnail",
    "fanart", "banner", "landscape", "portrait"
}

# Anime keywords (Chinese)
ANIME_KEYWORDS = {
    "动漫", "动画", "番剧", "国漫", "日漫", "anime"
}

# Drama keywords (Chinese)
DRAMA_KEYWORDS = {
    "短剧", "电视剧", "网剧", "drama"
}

# Pattern to match episode info at the end of folder names
EPISODE_PATTERN = re.compile(r'(?:[\.。·_]\s*(?:第\s*)?(\d+)\s*(?:集 | 话 | 話 | 期|EP|E|Episode)?\s*)+$', re.IGNORECASE)

# S01E01 style season+episode marker
SEASON_EPISODE_PATTERN = re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})')

# Single-episode-number markers: 第01集 / EP01 / E01 / 12期
EPISODE_NUMBER_PATTERN = re.compile(
    r'(?:第|EP?|集|話|话)\s*(\d+)\s*(?:集|話|话)?|(\d+)\s*期',
    re.IGNORECASE
)

# 第N季 inside a folder name
SEASON_FOLDER_PATTERN = re.compile(r'第\s*(\d+)\s*季')


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

    async def scan(self) -> Dict:
        """Main scan entry point."""
        self.items_scanned = 0
        self.items_added = 0

        if not self.media_root.exists():
            raise FileNotFoundError(f"Media root not found: {self.media_root}")

        # Group folders by main title
        media_groups = self._group_folders_by_title()

        # Process each group
        for title_key, folders in media_groups.items():
            await self._scan_media_group(title_key, folders)

        return {
            "items_scanned": self.items_scanned,
            "items_added": self.items_added
        }

    def _group_folders_by_title(self) -> Dict[str, List[Path]]:
        """Group folders by their main title (without episode info)."""
        groups = {}

        for item in sorted(self.media_root.iterdir()):
            if item.is_dir():
                title_key = self._extract_main_title(item.name)
                if title_key not in groups:
                    groups[title_key] = []
                groups[title_key].append(item)

        return groups

    def _extract_main_title(self, folder_name: str) -> str:
        """Extract main title from folder name, removing episode info."""
        # Remove episode patterns like ".第 01 集" or ".E01" or ".EP01"
        main_title = EPISODE_PATTERN.sub('', folder_name)

        # Also remove common separators at the end
        main_title = main_title.rstrip('.。·_ ')

        if not main_title:
            main_title = folder_name

        return main_title.lower()

    async def _scan_media_group(self, title_key: str, folders: List[Path]):
        """Scan a group of folders that belong to the same media."""
        # Use the first folder to get title info
        first_folder = folders[0]
        title_info = self._parse_folder_name(first_folder.name)

        # Determine type
        all_names = [f.name for f in folders]
        media_type = self._determine_type(' '.join(all_names))

        # 1. Find local cover first
        local_cover = self._find_cover_image(folders, title_info["clean_title"])

        # 2. Scrape metadata (CSPT/TMDB)
        meta_data = await self.scraper.scrape(title_info["display_title"])
        
        # 3. Process scraped data
        online_cover = None
        if meta_data.get("cover_url"):
            online_cover = await self.scraper.download_and_save_cover(
                meta_data["cover_url"], 
                title_info["clean_title"]
            )
        
        # Priority: Local > Online
        final_cover = local_cover or online_cover
        final_description = meta_data.get("description")
        final_year = meta_data.get("year") or title_info.get("year")
        final_rating = meta_data.get("rating")

        async with db.connect() as conn:
            # Check if already exists
            existing = await conn.execute(
                "SELECT id FROM media WHERE title_clean = ?",
                [title_info["clean_title"]]
            )
            existing_row = await existing.fetchone()

            if existing_row:
                media_id = existing_row["id"]
                # Clear old episodes for re-scan
                await conn.execute("DELETE FROM episodes WHERE media_id = ?", [media_id])
            else:
                # Insert new media
                result = await conn.execute(
                    """INSERT INTO media (
                        title, title_original, title_clean, title_pinyin, type, category,
                        year, total_episodes, cover_path, description, rating
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        title_info["display_title"],
                        first_folder.name,
                        title_info["clean_title"],
                        title_info.get("title_pinyin", ""),
                        media_type,
                        title_info.get("category"),
                        final_year,
                        0,
                        final_cover,
                        final_description,
                        final_rating
                    ]
                )
                await conn.commit()
                media_id = result.lastrowid
                self.items_added += 1

            self.items_scanned += len(folders)

            # Scan all folders in this group
            total_episodes = 0
            for folder in folders:
                episode_count = await self._scan_episodes(folder, media_id, total_episodes)
                total_episodes += episode_count

            # Update total episode count and cover path
            await conn.execute(
                """UPDATE media 
                   SET total_episodes = ?, cover_path = ?, description = ?, rating = ? 
                   WHERE id = ?""",
                [total_episodes, final_cover, final_description, final_rating, media_id]
            )
            await conn.commit()

    def _find_cover_image(self, folders: List[Path], title_key: str) -> Optional[str]:
        """Find and copy cover image from media folders."""
        # Priority 1: Look for standard cover names in any folder
        for folder in folders:
            for file in folder.iterdir():
                if file.is_file() and file.suffix.lower() in COVER_EXTENSIONS:
                    # Check if it's a known cover name
                    name_lower = file.stem.lower()
                    if name_lower in COVER_NAMES:
                        return self._copy_cover(file, title_key)

        # Priority 2: Look for any image file in the first folder
        if folders:
            for file in folders[0].iterdir():
                if file.is_file() and file.suffix.lower() in COVER_EXTENSIONS:
                    return self._copy_cover(file, title_key)

        # Priority 3: Look for image in parent directory (if folder has episode suffix)
        for folder in folders:
            parent_images = [
                f for f in folder.parent.iterdir()
                if f.is_file() and f.suffix.lower() in COVER_EXTENSIONS
            ]
            if parent_images:
                return self._copy_cover(parent_images[0], title_key)

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

    async def _scan_episodes(self, folder: Path, media_id: int, start_num: int) -> int:
        """Scan video files in a folder."""
        episodes = []
        episode_num = start_num + 1

        # Supported video files
        video_files = sorted([
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        ])

        async with db.connect() as conn:
            for video_file in video_files:
                file_size = video_file.stat().st_size
                episode_info = self._parse_episode_name(video_file.name, episode_num)

                await conn.execute(
                    """INSERT INTO episodes (
                        media_id, episode_number, title, file_path,
                        file_size, format
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    [
                        media_id,
                        episode_info["number"],
                        episode_info.get("title"),
                        str(video_file),
                        file_size,
                        video_file.suffix.lstrip(".").upper()
                    ]
                )
                await conn.commit()

                episodes.append(episode_info)
                episode_num += 1

        return len(episodes)

    def _parse_folder_name(self, name: str) -> Dict:
        """
        Parse media folder name to extract title, year, etc.
        Handles formats like:
        - 一剑破八荒.一剑斩妖邪.Yi.Jian.Po.Ba.Hua...
        - (新) 王牌神医.(Xin).Wang.Pai.Shen.Yi...
        - 18 岁太奶在线教训.18.Sui.Tai.Nai.Zai.Xia...
        """
        # Remove leading numbering/special chars
        clean = re.sub(r'^[（(] 新 [）)]\s*', '', name)

        # Remove episode info
        clean = EPISODE_PATTERN.sub('', clean)

        # Try to extract Chinese title (before first dot or special marker)
        parts = re.split(r'[.。·]', clean)

        # Get the first meaningful part as main title
        main_title = ""
        for part in parts:
            part = part.strip()
            if part and not part.isdigit():
                main_title = part
                break

        if not main_title:
            main_title = name

        # Try to extract year
        year_match = re.search(r'(20\d{2})', name)
        year = int(year_match.group(1)) if year_match else None

        # Clean title for search/indexing
        clean_title = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]', '', main_title).lower()

        # Generate pinyin for search
        title_pinyin = self._generate_pinyin(main_title)

        return {
            "display_title": main_title,
            "clean_title": clean_title,
            "title_pinyin": title_pinyin,
            "year": year,
        }

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

    def _infer_season_from_path(self, video_file: Path, show_folder: Path) -> int:
        """Look for a "第N季" marker in any folder between show_folder and the file."""
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

    def _determine_type(self, name: str) -> str:
        """Determine if media is anime or drama."""
        name_lower = name.lower()

        for keyword in DRAMA_KEYWORDS:
            if keyword.lower() in name_lower:
                return "drama"

        for keyword in ANIME_KEYWORDS:
            if keyword.lower() in name_lower:
                return "anime"

        # Default: try to guess based on patterns
        if any(c in name for c in ['番', '集', '话', '季']):
            return "anime"

        return "drama"  # Default to drama

    def _parse_episode_name(self, filename: str, fallback_num: int) -> Dict:
        """Parse episode filename to extract episode number and title."""
        # Remove extension
        base = Path(filename).stem

        # Try to find episode number patterns
        ep_match = re.search(
            r'(?:[Ee][Pp]?|第|集|話|话)\s*(\d+)',
            base,
            re.IGNORECASE
        )

        if ep_match:
            episode_num = int(ep_match.group(1))
            title = base[:ep_match.start()].strip()
            if not title:
                title = f"第 {episode_num} 集"
        else:
            episode_num = fallback_num
            title = base

        return {
            "number": episode_num,
            "title": title
        }
