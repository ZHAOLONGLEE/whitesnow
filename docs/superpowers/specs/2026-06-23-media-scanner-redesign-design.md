# 媒体扫描器重设计 — 支持真实目录结构

## 背景

WhiteSnow 实际部署在 QNAP NAS 上，真实媒体资源目录（`MEDIA_ROOT=/share/CACHEDEV1_DATA/BT`）结构是：

```
BT/
├── 国漫/                                              ← 一级：类型
│   └── 凡人修仙传/                                     ← 二级：剧名
│       └── 凡人修仙传.第1季.风云天南.1-21.全24集/         ← 三级：季/批次文件夹
│           └── 凡人修仙传...S01E01.2020.2160p...mkv     ← 视频文件
├── 短剧/
└── 综艺/
```

层级深度按类型不同（用户确认：国漫/短剧/综艺三类深度不一样，二级文件夹下到视频文件之间可能隔 0 到多层）。

现有 [scanner.py](../../backend/services/scanner.py) 的假设完全不匹配这个结构：
- 只扫 `MEDIA_ROOT` 的一级子目录，把它当成"一部剧的一个文件夹"
- 类型靠关键词在文件夹名里猜（`ANIME_KEYWORDS`/`DRAMA_KEYWORDS`）
- 视频文件假设直接放在那一级文件夹里，不递归
- 集数解析假设全剧连续编号，遇到多季会冲突（两季的 `S01E01`/`S02E01` 都会被解析成"第1集"）

本设计重写扫描逻辑以匹配真实结构，并同步调整数据模型、API、前端。

## 决策摘要

| 项目 | 决策 |
|------|------|
| 类型来源 | 一级文件夹名直接当 `type`（如"国漫"），删除关键词猜测逻辑 |
| 剧名来源 | 二级文件夹名直接当剧名，不再做"剥后缀再分组" |
| 视频文件查找 | 从二级文件夹递归查找，不限定层级深度 |
| 多季处理 | `episodes` 表加 `season` 列，全剧显示季号+本季集号，不再要求 `episode_number` 全局唯一 |
| 前端类型筛选 | 改成根据实际数据动态生成筛选按钮，不再硬编码"动漫/短剧"两个值 |

## 数据模型

`episodes` 表新增列（需要对已存在的表做迁移，`CREATE TABLE IF NOT EXISTS` 不会给已有表加列）：

```sql
ALTER TABLE episodes ADD COLUMN season INTEGER NOT NULL DEFAULT 1;
```

迁移方式：在 [database.py](../../backend/database.py) 的 `init_db()` 里，`executescript` 建表之后，额外执行一次 try/except 包裹的 `ALTER TABLE`（SQLite 不支持 `ADD COLUMN IF NOT EXISTS`，捕获"duplicate column"异常即可，幂等）。

`episode_number` 语义变化：从"全剧第几集"变成"本季第几集"。排序统一用 `(season, episode_number)`。

`media.type` 取值变化：从硬编码的 `"anime"/"drama"` 变成 NAS 上一级文件夹的原始名称（如 `"国漫"`、`"短剧"`、`"综艺"`），新增类型文件夹无需改代码。

## 扫描算法

替换 [scanner.py](../../backend/services/scanner.py) 中 `_group_folders_by_title` / `_extract_main_title` / `_determine_type` 整套基于关键词和文件夹名分组的逻辑。

```
scan():
    for category_folder in MEDIA_ROOT.iterdir():     # 一级：类型
        if not category_folder.is_dir(): continue
        type_name = category_folder.name
        for show_folder in category_folder.iterdir():  # 二级：剧名
            if not show_folder.is_dir(): continue
            process_show(type_name, show_folder)

process_show(type_name, show_folder):
    title = show_folder.name
    video_files = recursive_find_videos(show_folder)   # os.walk / rglob，不限层级
    if not video_files:
        return  # 空文件夹或非媒体文件夹，跳过

    cover = find_cover(show_folder)   # 根目录 + 直接子文件夹两层，不无限递归
    meta = await scraper.scrape(title)
    upsert media 行（title, type=type_name, cover, description, year, rating）

    delete 该 media_id 下所有旧 episodes（保持现有"重新扫描即覆盖"的行为）
    for file in video_files:
        season, episode_number, ep_title = parse_season_episode(file, show_folder)
        insert episode (media_id, season, episode_number, ep_title, file_path, file_size, format)

    update media.total_episodes = len(video_files)
```

### `parse_season_episode` 解析优先级

1. 文件名匹配 `S(\d{1,2})E(\d{1,3})`（大小写不敏感）→ 季号、集号直接取数字
2. 不匹配 1，但匹配 `第(\d+)集` / `EP?(\d+)` / `(\d+)期`（在原 `_parse_episode_name` 正则基础上补充"期"，支持综艺命名）→ 集号取数字；季号尝试从文件路径里任意一层文件夹名匹配 `第(\d+)季`，匹配不到则默认 1
3. 都不匹配 → 季号沿用上一步逻辑（默认 1 或从路径推断），集号在"同一季已发现的文件"内按顺序连续编号兜底

集标题（`ep_title`）：优先用 1/2 步骤里匹配位置之前的文件名部分（沿用现有 `_parse_episode_name` 的截取方式），解析失败则用文件名本身。

### 封面查找范围
`find_cover(show_folder)`：只查 `show_folder` 根目录 + 其直接子文件夹（季文件夹）这两层里的图片文件，不做无限递归（覆盖典型摆放位置，避免遍历整个剧的所有子目录找图片）。

## API 改动

- [media.py](../../backend/routers/media.py)：
  - `/api/media/{id}` 和 `/api/media/{id}/episodes` 的 `ORDER BY episode_number ASC` 改为 `ORDER BY season ASC, episode_number ASC`
  - 新增 `GET /api/media/types`：返回 `[{"type": "国漫", "count": 12}, ...]`，按 `type` group by 现有 `media` 表
- `/api/media?type=xxx` 过滤逻辑不变（本来就是任意字符串匹配）

## 前端改动

- [index.html](../../frontend/templates/index.html)：
  - 第 136-138 行硬编码的"全部/动漫/短剧"三个按钮，改成先调用 `/api/media/types` 拿到实际存在的类型列表再渲染（"全部"按钮固定保留，其余按类型列表动态生成）
  - 第 235-247 行单独 fetch anime/drama 数量的统计逻辑，改用 `/api/media/types` 的返回结果
  - 第 298 行 `item.type === 'anime' ? '动漫' : '短剧'` 三元表达式删除，直接显示 `item.type`
- [detail.html](../../frontend/templates/detail.html#L182) / [play.html](../../frontend/templates/play.html#L222)：`第 ${ep.episode_number} 集` 改成 `第${ep.season}季 第${ep.episode_number}集`，统一显示季号（即使只有一季也显示"第1季"，不做特判）

## 错误处理

- 类型/剧文件夹下没有任何视频文件（递归查找结果为空）：跳过，不创建 media 记录，沿用现有"容错跳过"风格
- 季号解析全部失败：默认季号 1，不报错、不中断整个扫描
- 元数据/封面抓取失败：沿用现有 try/except 返回 None 的容错方式，不影响本地已扫到的文件信息入库
- 重新扫描同一部剧：沿用现有"先删旧 episodes 再插入新的"覆盖逻辑

## 测试方式

仓库目前没有测试框架（无 `tests/` 目录，无 pytest 配置），本次不引入。改完后用以下方式手动验证（针对 NAS 上的真实目录结构）：

1. 跑一次扫描，检查 `/api/media` 和 `/api/media/{id}` 返回的 `type`、`season`、`episode_number` 是否符合预期
2. 重点验证多季剧：确认两季集数不互相覆盖、`episode_number` 在各自季内正确
3. 验证没有 `S01E01` 模式、只有"第N集"命名的短剧文件解析不受影响（回归检查）
4. 前端验证：筛选按钮能正确切换三个类型，详情页/播放页能看到"第X季 第Y集"

## 范围之外（本次不做）

- 不做跨季的全局连续集数编号
- 不做季选择 Tab／按季分组的详情页 UI，只在集标题里带季号文字
- 不处理"同一部剧分散在两个不同的二级文件夹"这种用户未提到的边界情况
- 不引入自动化测试框架
