# WhiteSnow QNAP NAS 部署指南

## 📋 目录
1. [环境准备](#环境准备)
2. [下载项目](#下载项目)
3. [配置路径映射](#配置路径映射)
4. [启动服务](#启动服务)
5. [常用维护命令](#常用维护命令)
6. [故障排查](#故障排查)

---

## 🚀 环境准备

### 前提条件
- QNAP NAS 已安装 **Container Station**
- 确保 NAS 固件版本较新（支持 Docker Compose）

### 检查 Docker 是否可用
```bash
docker --version
```

---

## 📥 下载项目

QNAP 系统默认没有 git，我们直接下载压缩包：

```bash
# 进入 Container 目录
cd /share/Container/

# 下载项目
wget https://github.com/ZHAOLONGLEE/whitesnow/archive/refs/heads/main.zip

# 解压
unzip main.zip

# 重命名文件夹
mv whitesnow-main whitesnow

# 进入项目目录
cd whitesnow

# 清理安装包
rm main.zip
```

---

## ️ 配置路径映射

### 1. 创建配置文件
```bash
cp .env.example .env
```

### 2. 修改媒体库路径
编辑 `.env` 文件中的 `MEDIA_ROOT`，指向你的媒体文件夹：

```bash
sed -i 's|^MEDIA_ROOT=.*|MEDIA_ROOT=/share/CACHEDEV1_DATA/BT|' .env
```

**路径说明：**
- 格式：`/share/CACHEDEV1_DATA/你的文件夹名`
- 示例：如果你的视频在 `/share/CACHEDEV1_DATA/BT/`，就填这个路径
- WhiteSnow 会自动扫描该目录下的一级子文件夹（如"动漫"、"短剧"等）

### 3. 处理端口冲突
如果 8080 或 8081 端口被占用，需要修改端口：

```bash
# 修改 docker-compose.yml 中的端口（例如改为 8888）
sed -i 's/8081/8888/g' docker-compose.yml
echo "APP_PORT=8888" >> .env
```

---

## ▶️ 启动服务

### 1. 创建必要目录
```bash
mkdir -p data static/covers static/icons
```

### 2. 构建镜像
```bash
sudo docker compose build
```
*(首次构建需要下载基础镜像，约 3-5 分钟)*

### 3. 启动容器
```bash
sudo docker compose up -d
```

### 4. 查看服务状态
```bash
sudo docker ps
```

---

##  访问地址

服务启动后，在浏览器访问：
```
http://<你的 NAS IP>:8888
```

**默认管理员账号：**
- 用户名：`admin`
- 密码：在 `.env` 文件中查看 `ADMIN_PASSWORD`

---

## ️ 常用维护命令

### 查看日志
```bash
sudo docker compose logs -f
```

### 停止服务
```bash
sudo docker compose down
```

### 重启服务
```bash
sudo docker compose restart
```

### 重新扫描媒体库
访问网页后，点击右上角 **"扫描媒体库"** 按钮。

### 更新项目
```bash
cd /share/Container/whitesnow
wget https://github.com/ZHAOLONGLEE/whitesnow/archive/refs/heads/main.zip
unzip -o main.zip
rsync -av whitesnow-main/ whitesnow/ --exclude='.git' --exclude='data' --exclude='static'
cd whitesnow
sudo docker compose up -d --build
```

---

## 🔍 故障排查

### 端口被占用
错误提示：`bind: address already in use`

**解决方法：**
1. 查看占用端口的进程：`sudo netstat -tlnp | grep 8081`
2. 修改 `docker-compose.yml` 中的端口映射
3. 重启服务

### 媒体库扫描失败
检查点：
1. 确认 `MEDIA_ROOT` 路径正确
2. 确认 Docker 容器有读取权限
3. 查看日志：`sudo docker compose logs backend`

### 视频无法播放
检查点：
1. 确认视频格式支持（MP4/MKV/AVI 等）
2. 确认文件路径中有中文字符（建议用英文或数字）
3. 查看浏览器控制台是否有 CORS 错误

---

## 📁 目录结构

```
/share/Container/whitesnow/
├── .env                    # 配置文件（含媒体路径）
├── docker-compose.yml      # Docker 配置
├── backend/                # FastAPI 后端
├── frontend/               # Jinja2 前端模板
├── nginx/                  # Nginx 反向代理
├── scripts/                # 部署脚本
├── data/                   # SQLite 数据库
├── static/                 # 静态资源（封面图等）
└── README.md               # 项目说明
```

---

## 📞 技术支持

- GitHub 仓库：https://github.com/ZHAOLONGLEE/whitesnow
- 问题反馈：在 GitHub Issues 中提交

---

*最后更新：2026-06-22*
