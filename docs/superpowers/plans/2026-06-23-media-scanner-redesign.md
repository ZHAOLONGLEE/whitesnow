# 媒体扫描器重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 [scanner.py](../../../backend/services/scanner.py) 支持真实的 NAS 目录结构（`类型/剧名/季批次文件夹.../视频文件`，深度按类型不同），并同步打通数据库、API、前端三层。

**Architecture:** 扫描器改成「一级=类型，二级=剧名，二级以下递归找视频文件」的两段式遍历；新增 `season` 字段解决多季集数冲突；类型不再靠关键词猜，直接用文件夹名；前端筛选/标签改成根据后端实际返回的类型动态生成。

**Tech Stack:** FastAPI + aiosqlite（后端），Jinja2 模板 + 原生 JS（前端）。无测试框架，验证靠本地构造的临时目录手动跑通。

**对应 Spec：** [docs/superpowers/specs/2026-06-23-media-scanner-redesign-design.md](../specs/2026-06-23-media-scanner-redesign-design.md)

---

## 文件结构总览

| 文件 | 改动类型 | 职责 |
|------|---------|------|
| `backend/database.py` | 修改 | 给 `episodes` 表加 `season` 列（迁移） |
| `backend/services/scanner.py` | 重写 | 递归扫描算法、季/集解析、封面查找 |
| `backend/routers/media.py` | 修改 | 排序改 `season,episode_number`；新增 `/api/media/types` |
| `frontend/templates/index.html` | 修改 | 筛选按钮/统计/标签改成动态类型列表 |
| `frontend/templates/detail.html` | 修改 | 集数展示加季号 |
| `frontend/templates/play.html` | 修改 | 集数展示加季号 |

环境说明：本地沙箱已执行 `pip install -r backend/requirements.txt`，可以直接 `python3 -c "..."` 验证后端逻辑，不需要起完整服务。

---

### Task 1: 数据库迁移 — `episodes` 表加 `season` 列

**Files:**
- Modify: `backend/database.py`

- [ ] **Step 1: 在 `init_db()` 的 `executescript` 之后加迁移逻辑**

打开 [backend/database.py](../../../backend/database.py)，在 `init_db` 方法的 `executescript(...)` 调用之后（仍在 `async with self.connect() as conn:` 块内），加入：

```python
            # Migration: episodes.season (added for multi-season shows)
            try:
                await conn.execute(
                    "ALTER TABLE episodes ADD COLUMN season INTEGER NOT NULL DEFAULT 1"
                )
                await conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
```

并在文件顶部加 `import sqlite3`（放在 `import aiosqlite` 旁边）：

```python
import aiosqlite
import sqlite3
import asyncio
```

- [ ] **Step 2: 验证迁移在全新库和已有库上都不报错**

```bash
cd /home/dev/whitesnow/backend
python3 -c "
import asyncio
import sys
from database import Database
from config import settings

async def main():
    settings.database_url = 'sqlite:////tmp/test_migration.db'
    db = Database()
    await db.init_db()
    await db.init_db()  # second call must not raise (column already exists)
    async with db.connect() as conn:
        cur = await conn.execute('PRAGMA table_info(episodes)')
        cols = [r[1] async for r in cur]
        assert 'season' in cols, cols
    print('OK: season column present, double-init did not raise')

asyncio.run(main())
"
rm -f /tmp/test_migration.db
```

Expected: `OK: season column present, double-init did not raise`

- [ ] **Step 3: Commit**

```bash
cd /home/dev/whitesnow
git add backend/database.py
git commit -m "$(cat <<'EOF'
Add season column migration to episodes table

Needed so multi-season shows don't collide on episode_number (two
seasons both having an S01E01/S02E01-style episode 1 would otherwise
overwrite each other's numbering).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Scanner — 季/集文件名解析函数

**Files:**
- Modify: `backend/services/scanner.py`

- [ ] **Step 1: 在文件顶部正则区域加三个新正则**

在现有 `EPISODE_PATTERN = re.compile(...)` 那一行**下面**加：

```python
# S01E01 style season+episode marker
SEASON_EPISODE_PATTERN = re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})')

# Single-episode-number markers: 第01集 / EP01 / E01 / 12期
EPISODE_NUMBER_PATTERN = re.compile(
    r'(?:第|EP?|集|話|话)\s*(\d+)\s*(?:集|話|话)?|(\d+)\s*期',
    re.IGNORECASE
)

# 第N季 inside a folder name
SEASON_FOLDER_PATTERN = re.compile(r'第\s*(\d+)\s*季')
```

- [ ] **Step 2: 在 `MediaScanner` 类里加两个新方法**

加在 `_generate_pinyin` 方法旁边（类内任意位置都行，建议紧跟在一起）：

```python
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
```

确认 `from typing import Dict, List, Optional, Tuple` 已经包含 `Tuple`（现有 import 已经有，不需要改）。

- [ ] **Step 3: 写验证脚本，跑通三种命名场景**

```bash
cd /home/dev/whitesnow/backend
python3 -c "
from pathlib import Path
from services.scanner import MediaScanner

scanner = MediaScanner.__new__(MediaScanner)  # skip __init__ (no FS side effects needed)
show = Path('/tmp/凡人修仙传')

# Case 1: S01E01 style, season 1
f1 = show / '凡人修仙传.第1季.风云天南.1-21.全24集' / \"凡人修仙传.A.Record.S01E01.2020.2160p.mkv\"
r1 = scanner._parse_season_episode(f1, show, {})
assert r1[0] == 1 and r1[1] == 1, r1
print('case1 S01E01 ->', r1)

# Case 2: same show, season 2, must not collide with season 1 episode 1
f2 = show / '凡人修仙传.第2季' / \"凡人修仙传.S02E01.2021.2160p.mkv\"
r2 = scanner._parse_season_episode(f2, show, {})
assert r2[0] == 2 and r2[1] == 1, r2
print('case2 S02E01 ->', r2)

# Case 3: plain Chinese drama naming, no season marker -> season defaults to 1
f3 = show / '老地方的我们' / '老地方的我们.第03集.mp4'
r3 = scanner._parse_season_episode(f3, show, {})
assert r3[0] == 1 and r3[1] == 3, r3
print('case3 第03集 ->', r3)

# Case 4: variety show with 期
f4 = show / '某综艺' / '某综艺.第36期.mp4'
r4 = scanner._parse_season_episode(f4, show, {})
assert r4[0] == 1 and r4[1] == 36, r4
print('case4 第36期 ->', r4)

# Case 5: nothing parseable -> sequential fallback within season
counters = {}
f5a = show / '杂项' / 'random_name_a.mp4'
f5b = show / '杂项' / 'random_name_b.mp4'
r5a = scanner._parse_season_episode(f5a, show, counters)
r5b = scanner._parse_season_episode(f5b, show, counters)
assert r5a[1] == 1 and r5b[1] == 2, (r5a, r5b)
print('case5 fallback ->', r5a, r5b)

print('ALL PARSE CASES OK')
"
```

Expected: 打印每个 case 的结果，最后一行 `ALL PARSE CASES OK`，过程中任何 `assert` 失败说明正则或优先级有问题，需要先修好才能继续。

- [ ] **Step 4: Commit**

```bash
cd /home/dev/whitesnow
git add backend/services/scanner.py
git commit -m "$(cat <<'EOF'
Add season-aware episode filename parser to scanner

Parses S01E01-style markers first (captures season explicitly),
falls back to plain episode-number patterns (第N集/EPxx/N期) with
season inferred from a 第N季 folder name, and finally a sequential
counter per season if nothing in the filename is parseable.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Scanner — 递归找视频文件 + 封面查找范围调整

**Files:**
- Modify: `backend/services/scanner.py`

- [ ] **Step 1: 加递归视频文件查找方法**

加在 `_parse_season_episode` 旁边：

```python
    def _find_video_files(self, show_folder: Path) -> List[Path]:
        """Recursively find every video file under a show folder, any depth."""
        return sorted(
            p for p in show_folder.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        )
```

- [ ] **Step 2: 重写 `_find_cover_image`，改成「剧文件夹根目录 + 直接子文件夹」两层查找**

找到现有的 `_find_cover_image` 方法（接收 `folders: List[Path], title_key: str`），整个替换成：

```python
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
```

`_copy_cover` 方法不用改，原样保留。

- [ ] **Step 3: 验证递归查找和封面查找**

```bash
cd /home/dev/whitesnow/backend
rm -rf /tmp/scan_test && mkdir -p \
  "/tmp/scan_test/国漫/凡人修仙传/凡人修仙传.第1季.风云天南.1-21.全24集" \
  "/tmp/scan_test/国漫/凡人修仙传/凡人修仙传.第2季" \
  "/tmp/scan_test/短剧/老地方的我们"

touch "/tmp/scan_test/国漫/凡人修仙传/凡人修仙传.第1季.风云天南.1-21.全24集/凡人修仙传.S01E01.2020.mkv"
touch "/tmp/scan_test/国漫/凡人修仙传/凡人修仙传.第2季/凡人修仙传.S02E01.2021.mkv"
touch "/tmp/scan_test/短剧/老地方的我们/老地方的我们.第01集.mp4"
echo fake_jpg > "/tmp/scan_test/国漫/凡人修仙传/poster.jpg"

python3 -c "
from pathlib import Path
from services.scanner import MediaScanner

scanner = MediaScanner.__new__(MediaScanner)
scanner.cover_storage = Path('/tmp/scan_test_covers')
scanner.cover_storage.mkdir(exist_ok=True)

show = Path('/tmp/scan_test/国漫/凡人修仙传')
videos = scanner._find_video_files(show)
assert len(videos) == 2, videos
print('found videos:', [str(v) for v in videos])

cover = scanner._find_cover_image(show, 'fanrenxiuxianzhuan')
assert cover == '/static/covers/fanrenxiuxianzhuan.jpg', cover
print('cover ->', cover)
print('OK')
"
rm -rf /tmp/scan_test /tmp/scan_test_covers
```

Expected: 打印 2 个视频文件路径，`cover -> /static/covers/fanrenxiuxianzhuan.jpg`，最后 `OK`。

- [ ] **Step 4: Commit**

```bash
cd /home/dev/whitesnow
git add backend/services/scanner.py
git commit -m "$(cat <<'EOF'
Make scanner recurse for video files; bound cover search to 2 levels

Episode files can be nested arbitrarily deep under a show folder
(season/batch subfolders), so video discovery now walks the whole
subtree. Cover lookup stays bounded to the show folder root and its
direct subfolders to avoid scanning the entire subtree for images.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Scanner — 重写主扫描流程，删除旧的关键词/分组逻辑

**Files:**
- Modify: `backend/services/scanner.py`

- [ ] **Step 1: 删除以下不再需要的代码**

从 [backend/services/scanner.py](../../../backend/services/scanner.py) 里删除：
- `ANIME_KEYWORDS` 和 `DRAMA_KEYWORDS` 这两个 set 定义
- `EPISODE_PATTERN` 这个正则（旧的"剥掉文件夹名里集数后缀"用的，新逻辑不需要）
- `_group_folders_by_title` 方法
- `_extract_main_title` 方法
- `_determine_type` 方法
- 旧的 `_parse_folder_name` 方法
- 旧的 `_parse_episode_name` 方法（已被 Task 2 的 `_parse_season_episode` 取代）
- 旧的 `_scan_episodes` 方法（逐文件夹扫描，已被新流程取代）
- 旧的 `_scan_media_group` 方法（按分组扫描，已被新的 `_process_show` 取代，下一步会加）

文件顶部 `import os` 和 `import asyncio` 这两行不再被任何代码使用，一并删除。

- [ ] **Step 2: 重写 `scan()` 方法**

```python
    async def scan(self) -> Dict:
        """Main scan entry point."""
        self.items_scanned = 0
        self.items_added = 0

        if not self.media_root.exists():
            raise FileNotFoundError(f"Media root not found: {self.media_root}")

        for category_folder in sorted(self.media_root.iterdir()):
            if not category_folder.is_dir():
                continue
            type_name = category_folder.name

            for show_folder in sorted(category_folder.iterdir()):
                if not show_folder.is_dir():
                    continue
                await self._process_show(type_name, show_folder)

        return {
            "items_scanned": self.items_scanned,
            "items_added": self.items_added
        }
```

- [ ] **Step 3: 加 `_process_show` 方法（取代旧的 `_scan_media_group` + `_scan_episodes`）**

```python
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
                        year, total_episodes, cover_path, description, rating
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        title, show_folder.name, clean_title, title_pinyin, type_name, None,
                        final_year, 0, final_cover, final_description, final_rating
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
                   SET total_episodes = ?, type = ?, cover_path = ?, description = ?, rating = ?
                   WHERE id = ?""",
                [len(video_files), type_name, final_cover, final_description, final_rating, media_id]
            )
            await conn.commit()
```

- [ ] **Step 4: 端到端验证 — 用构造的临时目录跑真实的 `scan()`**

```bash
cd /home/dev/whitesnow/backend
rm -rf /tmp/e2e_scan && mkdir -p \
  "/tmp/e2e_scan/国漫/凡人修仙传/凡人修仙传.第1季.风云天南.1-21.全24集" \
  "/tmp/e2e_scan/国漫/凡人修仙传/凡人修仙传.第2季" \
  "/tmp/e2e_scan/短剧/老地方的我们" \
  "/tmp/e2e_scan/综艺/某综艺"

touch "/tmp/e2e_scan/国漫/凡人修仙传/凡人修仙传.第1季.风云天南.1-21.全24集/凡人修仙传.S01E01.2020.mkv"
touch "/tmp/e2e_scan/国漫/凡人修仙传/凡人修仙传.第1季.风云天南.1-21.全24集/凡人修仙传.S01E02.2020.mkv"
touch "/tmp/e2e_scan/国漫/凡人修仙传/凡人修仙传.第2季/凡人修仙传.S02E01.2021.mkv"
touch "/tmp/e2e_scan/短剧/老地方的我们/老地方的我们.第01集.mp4"
touch "/tmp/e2e_scan/短剧/老地方的我们/老地方的我们.第02集.mp4"
touch "/tmp/e2e_scan/综艺/某综艺/某综艺.第36期.mp4"
rm -f /tmp/e2e_scan.db

python3 -c "
import asyncio
from pathlib import Path
from config import settings
from database import db
from services.scanner import MediaScanner

async def main():
    settings.media_root = '/tmp/e2e_scan'
    settings.cover_storage = '/tmp/e2e_scan_covers'
    settings.database_url = 'sqlite:////tmp/e2e_scan.db'

    await db.init_db()

    scanner = MediaScanner()
    result = await scanner.scan()
    print('scan result:', result)
    assert result['items_added'] == 3, result

    async with db.connect() as conn:
        rows = await conn.execute('SELECT title, type, total_episodes FROM media ORDER BY title')
        media_rows = [dict(r) async for r in rows]
        print('media:', media_rows)
        assert len(media_rows) == 3
        types = {r['type'] for r in media_rows}
        assert types == {'国漫', '短剧', '综艺'}, types

        fan = next(r for r in media_rows if '凡人' in r['title'])
        ep_rows_cur = await conn.execute(
            'SELECT season, episode_number FROM episodes e JOIN media m ON e.media_id = m.id WHERE m.title_clean = ? ORDER BY season, episode_number',
            [fan['title'].lower()]
        )
        eps = [dict(r) async for r in ep_rows_cur]
        print('凡人修仙传 episodes:', eps)
        assert eps == [
            {'season': 1, 'episode_number': 1},
            {'season': 1, 'episode_number': 2},
            {'season': 2, 'episode_number': 1},
        ], eps

    print('E2E SCAN OK')

asyncio.run(main())
"
rm -rf /tmp/e2e_scan /tmp/e2e_scan_covers /tmp/e2e_scan.db
```

Expected: 打印 `scan result: {'items_scanned': 3, 'items_added': 3}`，三条 media 记录的 `type` 分别是 国漫/短剧/综艺，凡人修仙传的三集 season/episode_number 严格是 `(1,1) (1,2) (2,1)`（季 1、2 不冲突），最后一行 `E2E SCAN OK`。任何 assert 失败说明扫描逻辑有 bug，必须先修好才能进入下一步。

- [ ] **Step 5: Commit**

```bash
cd /home/dev/whitesnow
git add backend/services/scanner.py
git commit -m "$(cat <<'EOF'
Rewrite scanner to walk category/show/season-batch/file structure

Replaces the old single-level "group sibling folders by stripped
title" approach (which assumed videos sit directly in a top-level
folder, and guessed type via keywords) with: category folder name as
type, show folder name as title, recursive video discovery beneath
it, season-aware episode numbering via the Task 2 parser.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: API — 排序改 `season,episode_number` + 新增 `/api/media/types`

**Files:**
- Modify: `backend/routers/media.py`

- [ ] **Step 1: 加 `/api/media/types` 路由**

在 `list_media`（`/media` 路由）函数**之后**、`get_media`（`/media/{media_id}` 路由）函数**之前**插入（顺序很重要：必须在 `/media/{media_id}` 之前注册，否则 `/media/types` 会被当成 `media_id=types` 走到详情路由，FastAPI 对 `int` 类型转换失败返回 422）：

```python
@router.get("/media/types")
async def list_types():
    """List distinct media types with counts, for dynamic filter UI."""
    async with db.connect() as conn:
        rows = await conn.execute(
            "SELECT type, COUNT(*) as count FROM media GROUP BY type ORDER BY type"
        )
        items = [dict(row) for row in await rows.fetchall()]
    return {"items": items}
```

- [ ] **Step 2: 改两处 `ORDER BY`**

`get_media` 函数里：
```python
        episodes_row = await conn.execute(
            "SELECT * FROM episodes WHERE media_id = ? ORDER BY episode_number ASC",
            [media_id]
        )
```
改成：
```python
        episodes_row = await conn.execute(
            "SELECT * FROM episodes WHERE media_id = ? ORDER BY season ASC, episode_number ASC",
            [media_id]
        )
```

`list_episodes` 函数里同样的 `ORDER BY episode_number ASC` 改成 `ORDER BY season ASC, episode_number ASC`。

- [ ] **Step 3: 验证路由注册顺序**

```bash
cd /home/dev/whitesnow/backend
python3 -c "
from routers import media
paths = [r.path for r in media.router.routes]
print(paths)
type_idx = paths.index('/media/types')
detail_idx = paths.index('/media/{media_id}')
assert type_idx < detail_idx, '/media/types must be registered before /media/{media_id}'
print('ROUTE ORDER OK')
"
```

Expected: 打印路由路径列表，最后一行 `ROUTE ORDER OK`。

- [ ] **Step 4: Commit**

```bash
cd /home/dev/whitesnow
git add backend/routers/media.py
git commit -m "$(cat <<'EOF'
Sort episodes by season then episode_number; add /api/media/types

The new season column means episode_number alone is no longer a
stable sort key across multi-season shows. /api/media/types backs
the frontend's dynamic filter buttons instead of hardcoding anime/drama.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 前端 — index.html 筛选/统计/标签动态化

**Files:**
- Modify: `frontend/templates/index.html`

- [ ] **Step 1: 改筛选按钮区域（第 135-139 行）**

把：
```html
        <div class="flex items-center justify-between mb-8">
            <div class="flex items-center gap-2">
                <button onclick="setFilter('')" class="filter-btn px-5 py-2.5 rounded-xl text-sm font-medium active" data-type="">全部</button>
                <button onclick="setFilter('anime')" class="filter-btn px-5 py-2.5 rounded-xl text-sm font-medium" data-type="anime">动漫</button>
                <button onclick="setFilter('drama')" class="filter-btn px-5 py-2.5 rounded-xl text-sm font-medium" data-type="drama">短剧</button>
            </div>
            <div class="text-sm text-white/30" id="resultCount"></div>
        </div>
```
改成：
```html
        <div class="flex items-center justify-between mb-8">
            <div id="filterButtons" class="flex items-center gap-2 flex-wrap">
                <button onclick="setFilter('')" class="filter-btn px-5 py-2.5 rounded-xl text-sm font-medium active" data-type="">全部</button>
            </div>
            <div class="text-sm text-white/30" id="resultCount"></div>
        </div>
```

- [ ] **Step 2: 改统计卡片区域（第 82-88 行的两个动漫/短剧卡片）**

把：
```html
                    <div class="stat-card bg-white/[0.04] border border-white/[0.06] rounded-2xl p-6">
                        <div class="text-3xl font-semibold text-white/80 mb-1" id="statAnime">-</div>
                        <div class="text-sm text-white/40">动漫</div>
                    </div>
                    <div class="stat-card bg-white/[0.04] border border-white/[0.06] rounded-2xl p-6">
                        <div class="text-3xl font-semibold text-white/80 mb-1" id="statDrama">-</div>
                        <div class="text-sm text-white/40">短剧</div>
                    </div>
```
改成：
```html
                    <div id="statTypesContainer" class="col-span-2 flex flex-wrap gap-3"></div>
```

- [ ] **Step 3: 改 Hero CTA 链接，去掉硬编码的 `?type=anime`（第 63 行）**

把：
```html
                        <a href="?type=anime" class="btn-secondary px-7 py-3 rounded-2xl text-sm font-medium flex items-center gap-2">
```
改成：
```html
                        <a href="#library" class="btn-secondary px-7 py-3 rounded-2xl text-sm font-medium flex items-center gap-2">
```

并给 Media Library Section 的 `<section>` 标签加上对应锚点 id（第 131-132 行附近）。把：
```html
    <!-- Media Library Section -->
    <section class="max-w-[1400px] mx-auto px-6 lg:px-8 pb-16">
```
改成：
```html
    <!-- Media Library Section -->
    <section id="library" class="max-w-[1400px] mx-auto px-6 lg:px-8 pb-16">
```

- [ ] **Step 4: 改 JS — 去掉脚本顶部立即同步筛选按钮状态的代码（第 198-211 行）**

把：
```javascript
        // State
        currentType = new URLSearchParams(window.location.search).get('type');
        currentSearch = new URLSearchParams(window.location.search).get('search') || '';
        
        if (currentSearch) {
            document.getElementById('searchInput').value = currentSearch;
        }

        // Set filter from URL
        if (currentType) {
            document.querySelectorAll('.filter-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.type === currentType);
            });
        }

        function setFilter(type) {
```
改成（去掉"Set filter from URL"那一段，因为按类型的按钮现在是动态创建的，创建之前不存在，挪到 Step 5 的 `renderTypeUI` 里统一处理）：
```javascript
        // State
        currentType = new URLSearchParams(window.location.search).get('type');
        currentSearch = new URLSearchParams(window.location.search).get('search') || '';
        
        if (currentSearch) {
            document.getElementById('searchInput').value = currentSearch;
        }

        function setFilter(type) {
```

- [ ] **Step 5: 改 `loadStats`，加 `renderTypeUI`（第 232-260 行）**

把整个 `loadStats` 函数：
```javascript
        // Load stats
        async function loadStats() {
            try {
                const [allRes, animeRes, dramaRes] = await Promise.all([
                    fetch('/api/media?page=1&page_size=1'),
                    fetch('/api/media?type=anime&page=1&page_size=1'),
                    fetch('/api/media?type=drama&page=1&page_size=1')
                ]);
                
                const [allData, animeData, dramaData] = await Promise.all([
                    allRes.json(), animeRes.json(), dramaRes.json()
                ]);

                document.getElementById('statTotal').textContent = allData.total || '0';
                document.getElementById('statAnime').textContent = animeData.total || '0';
                document.getElementById('statDrama').textContent = dramaData.total || '0';

                // Calculate total episodes
                let totalEpisodes = 0;
                if (allData.total > 0) {
                    const allItemsRes = await fetch(`/api/media?page=1&page_size=${Math.min(allData.total, 100)}`);
                    const allItemsData = await allItemsRes.json();
                    totalEpisodes = allItemsData.items.reduce((sum, item) => sum + (item.episode_count || 0), 0);
                }
                document.getElementById('statEpisodes').textContent = totalEpisodes || '0';
            } catch (error) {
                console.error('Failed to load stats:', error);
            }
        }
```
改成：
```javascript
        // Render filter buttons + stat chips from the actual types in the database
        function renderTypeUI(items) {
            const filterContainer = document.getElementById('filterButtons');
            items.forEach(t => {
                const btn = document.createElement('button');
                btn.className = 'filter-btn px-5 py-2.5 rounded-xl text-sm font-medium';
                btn.dataset.type = t.type;
                btn.textContent = t.type;
                btn.onclick = () => setFilter(t.type);
                filterContainer.appendChild(btn);
            });

            document.querySelectorAll('.filter-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.type === (currentType || ''));
            });

            document.getElementById('statTypesContainer').innerHTML = items.map(t => `
                <div class="stat-card bg-white/[0.04] border border-white/[0.06] rounded-2xl px-5 py-3 flex items-center gap-2">
                    <span class="text-lg font-semibold text-white/80">${t.count}</span>
                    <span class="text-sm text-white/40">${t.type}</span>
                </div>
            `).join('');
        }

        // Load stats
        async function loadStats() {
            try {
                const [typesRes, allRes] = await Promise.all([
                    fetch('/api/media/types'),
                    fetch('/api/media?page=1&page_size=1')
                ]);
                const typesData = await typesRes.json();
                const allData = await allRes.json();

                renderTypeUI(typesData.items);
                document.getElementById('statTotal').textContent = allData.total || '0';

                // Calculate total episodes
                let totalEpisodes = 0;
                if (allData.total > 0) {
                    const allItemsRes = await fetch(`/api/media?page=1&page_size=${Math.min(allData.total, 100)}`);
                    const allItemsData = await allItemsRes.json();
                    totalEpisodes = allItemsData.items.reduce((sum, item) => sum + (item.episode_count || 0), 0);
                }
                document.getElementById('statEpisodes').textContent = totalEpisodes || '0';
            } catch (error) {
                console.error('Failed to load stats:', error);
            }
        }
```

- [ ] **Step 6: 去掉卡片标签的二选一三元表达式**

找到（原第 298 行）：
```javascript
                        <span class="text-xs text-white/25">${item.type === 'anime' ? '动漫' : '短剧'}</span>
```
改成：
```javascript
                        <span class="text-xs text-white/25">${item.type}</span>
```

- [ ] **Step 7: 验证模板语法没问题**

```bash
cd /home/dev/whitesnow
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('frontend/templates'))
tmpl = env.get_template('index.html')
print('Jinja2 parse OK')
"
```

Expected: `Jinja2 parse OK`（这一步只能检查模板语法，检查不出 JS 字符串拼接逻辑对不对，逻辑正确性靠后面 Task 8 在真实页面里点一遍）。

- [ ] **Step 8: Commit**

```bash
git add frontend/templates/index.html
git commit -m "$(cat <<'EOF'
Make index.html filter buttons and labels type-agnostic

Replaces the hardcoded anime/drama filter buttons, stat cards, and
card label ternary with a render pass driven by /api/media/types, so
any category folder name (国漫/短剧/综艺/...) works without further
frontend changes.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 前端 — detail.html / play.html 显示季号

**Files:**
- Modify: `frontend/templates/detail.html`
- Modify: `frontend/templates/play.html`

- [ ] **Step 1: `detail.html` 改集数展示（第 182 行）**

把：
```javascript
                            <span class="text-sm font-medium text-white/70 group-hover:text-white transition-colors">
                                第 ${ep.episode_number} 集
                            </span>
```
改成：
```javascript
                            <span class="text-sm font-medium text-white/70 group-hover:text-white transition-colors">
                                第${ep.season}季 第 ${ep.episode_number} 集
                            </span>
```

注意：第 189 行的 `` ep.title !== `第 ${ep.episode_number} 集` `` 这个比较**不要改**——它是用来判断"是否要额外显示一行 episode title"的去重逻辑，后端生成的 fallback title 格式就是这个不带季号的样子，改了反而会让本该隐藏的标题又显示出来。

- [ ] **Step 2: `play.html` 改集数展示（第 222 行）**

把：
```javascript
                        <span class="episode-title text-sm font-medium ${index === currentIndex ? 'text-leaf' : 'text-white/60'}">
                            第 ${ep.episode_number} 集
                        </span>
```
改成：
```javascript
                        <span class="episode-title text-sm font-medium ${index === currentIndex ? 'text-leaf' : 'text-white/60'}">
                            第${ep.season}季 第 ${ep.episode_number} 集
                        </span>
```

第 226 行 `` ep.title !== `第 ${ep.episode_number} 集` `` 同样**不要改**，原因同上。第 252 行的 `` ep.title || `第 ${ep.episode_number} 集` ``（当前播放标题的 fallback）也不要改，它只是 fallback 文案，不影响功能。

- [ ] **Step 3: 验证模板语法**

```bash
cd /home/dev/whitesnow
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('frontend/templates'))
env.get_template('detail.html')
env.get_template('play.html')
print('Jinja2 parse OK for both')
"
```

Expected: `Jinja2 parse OK for both`

- [ ] **Step 4: Commit**

```bash
git add frontend/templates/detail.html frontend/templates/play.html
git commit -m "$(cat <<'EOF'
Show season number alongside episode number in detail/play pages

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 推送 + NAS 上的人工验证清单

**Files:** 无代码改动，仅推送和验证。

- [ ] **Step 1: 推送前最后过一遍 diff**

```bash
cd /home/dev/whitesnow
git status
git log --oneline -8
git diff HEAD~7..HEAD --stat
```

`HEAD~7` 对应 Task 1-7 一共 7 次 commit（每个 Task 末尾各一次）；如果中途有合并或拆分了 commit 数量，把 `7` 换成实际数量。确认改动范围只覆盖了 Task 1-7 列的那 6 个文件，没有意外改到别的地方，且 `git status` 是 clean 的（没有遗漏的未提交改动）。

- [ ] **Step 2: 推送到 `main`**

```bash
git push origin main
```

推送后 NAS 上的 self-hosted runner 会自动拉取部署（参考 [docs/CI_CD.md](../../CI_CD.md)）。

- [ ] **Step 3: NAS 上的验证清单（人工执行，对照真实数据）**

```bash
# 1. 触发一次扫描（网页右上角点「扫描媒体库」，或直接调接口）——扫描是后台任务，发起后立刻返回
curl -X POST http://localhost:8888/api/scan/start

# 2. 轮询扫描状态，直到 status 变成 completed（或 failed，看报错）
curl -s http://localhost:8888/api/scan/status | python3 -m json.tool

# 3. 看类型列表是不是国漫/短剧/综艺三个，count 是否合理
curl -s http://localhost:8888/api/media/types | python3 -m json.tool

# 4. 找一部确认有多季的剧，看 season 字段对不对
curl -s "http://localhost:8888/api/media?search=凡人修仙传" | python3 -m json.tool
```

- 网页首页：筛选按钮是不是显示了「全部/国漫/短剧/综艺」，点每个按钮能不能正常筛选
- 任意一部多季剧的详情页：集数列表是否显示「第1季 第1集」「第2季 第1集」，没有编号冲突/重复
- 任意一部短剧（纯"第N集"命名，没有 S01E01）：确认没有因为新加的 SxxExx 优先级而解析错误
- 任意一部综艺（"期"命名）：确认能正常入库和播放

有问题就把报错或者截图发回来，针对性修。

---

## Plan Self-Review 记录

- **Spec coverage**：数据模型（Task 1）、扫描算法+季解析+封面范围（Task 2-4）、API 排序+新接口（Task 5）、前端三处改动（Task 6-7）、手动验证清单（Task 8）——spec 里列的六项决策摘要都有对应任务覆盖。
- **Placeholder scan**：每个 Step 都是完整代码块和可执行命令，没有 TBD/"加适当的处理"这类占位描述。
- **Type/命名一致性**：`_parse_season_episode` / `_find_video_files` / `_find_cover_image` / `_process_show` 这几个新方法名在 Task 2-4 之间保持一致，Task 4 的 `scan()`/`_process_show()` 调用的方法名和 Task 2-3 定义的完全匹配。
