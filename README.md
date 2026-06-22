# WhiteSnow ❄️

家庭媒体中心 — 本地 NAS 动漫/短剧在线观看与下载服务

## 功能特性

- 浏览 NAS 中的媒体库（海报墙）
- 在线观看（流式播放，支持进度条、倍速）
- 直接下载原文件
- 自动扫描 NAS 目录，解析文件名
- 播放进度记忆
- 搜索与筛选（动漫/短剧）

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，设置你的 NAS 媒体库路径：

```env
MEDIA_ROOT=/path/to/your/nas/media
```

### 2. 启动服务（开发模式）

```bash
docker compose up -d
```

访问 http://localhost:8080

### 3. 扫描媒体库

点击页面右上角的 **🔄 扫描媒体库** 按钮，系统会自动扫描 NAS 目录并建立索引。

### 4. 生产部署

```bash
docker compose -f docker-compose.prod.yml up -d
```

## 项目结构

```
whitesnow/
├── backend/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置管理
│   ├── database.py          # SQLite 数据库
│   ├── requirements.txt     # Python 依赖
│   ├── Dockerfile
│   ├── routers/
│   │   ├── media.py         # 媒体库 API
│   │   ├── play.py          # 播放 API
│   │   ├── download.py      # 下载 API
│   │   └── scan.py          # 扫描 API
│   └── services/
│       └── scanner.py       # 媒体扫描服务
├── frontend/
│   ├── templates/
│   │   ├── base.html        # 基础模板
│   │   ├── index.html       # 海报墙首页
│   │   ├── detail.html      # 媒体详情页
│   │   └── play.html        # 播放页
│   ── static/              # 静态资源
├── data/                    # SQLite 数据库文件
├── nginx/                   # Nginx 配置
├── docker-compose.yml       # 开发环境
└── docker-compose.prod.yml  # 生产环境
```

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python + FastAPI |
| 前端 | Jinja2 + TailwindCSS |
| 数据库 | SQLite (aiosqlite) |
| 播放器 | Video.js |
| 部署 | Docker Compose + Nginx |

## 媒体文件命名规范

系统支持以下命名格式（自动解析）：

- `剧名.拼音.E01.mp4`
- `剧名.第01集.mp4`
- `剧名.EP01.mp4`
- `剧名.01.mp4`

类型自动识别：包含"动漫/动画/番剧"关键词的识别为动漫，包含"短剧/电视剧"的识别为短剧。

## 许可证

MIT
