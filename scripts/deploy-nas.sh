#!/bin/bash
# WhiteSnow QNAP 部署脚本
# 在 QNAP NAS 上通过 SSH 运行此脚本

set -e

echo "=========================================="
echo " WhiteSnow QNAP 部署脚本"
echo "=========================================="

# 配置
PROJECT_DIR="/share/Container/whitesnow"
MEDIA_DIR="/share/Media"  # 根据你的实际媒体目录调整
APP_PORT=8080

# 检查 Docker 是否可用
if ! command -v docker &> /dev/null; then
    echo " 未找到 Docker！请从 QNAP 应用中心安装 Container Station。"
    exit 1
fi

echo "✅ 找到 Docker: $(docker --version)"

# 创建项目目录
echo ""
echo " 创建项目目录: $PROJECT_DIR"
mkdir -p "$PROJECT_DIR"

# 复制项目文件（假设脚本在项目根目录运行）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo " 复制项目文件..."
cp -r "$PROJECT_ROOT/backend" "$PROJECT_DIR/"
cp -r "$PROJECT_ROOT/frontend" "$PROJECT_DIR/"
cp -r "$PROJECT_ROOT/nginx" "$PROJECT_DIR/"
cp "$PROJECT_ROOT/docker-compose.yml" "$PROJECT_DIR/"
cp "$PROJECT_ROOT/docker-compose.prod.yml" "$PROJECT_DIR/"
cp "$PROJECT_ROOT/.env.example" "$PROJECT_DIR/.env"
cp "$PROJECT_ROOT/.gitignore" "$PROJECT_DIR/"
cp "$PROJECT_ROOT/README.md" "$PROJECT_DIR/"

# 创建必要目录
mkdir -p "$PROJECT_DIR/data"
mkdir -p "$PROJECT_DIR/static/covers"
mkdir -p "$PROJECT_DIR/static/icons"

# 更新 .env 文件
echo ""
echo "️ 配置环境变量..."
cat > "$PROJECT_DIR/.env" << EOF
# WhiteSnow 环境变量

# 应用
APP_ENV=production
APP_SECRET=$(openssl rand -hex 32 2>/dev/null || echo "change-me-to-random-string")

# NAS 媒体库路径
MEDIA_ROOT=${MEDIA_DIR}
MEDIA_MOUNT=/media

# 数据库
DATABASE_URL=sqlite:///./data/mediascan.db

# 服务器
HOST=0.0.0.0
PORT=8000

# 管理员
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$(openssl rand -base64 12 2>/dev/null || echo "changeme123")
EOF

# 创建 QNAP 专用 docker-compose
echo ""
echo " 创建 Docker Compose 配置..."
cat > "$PROJECT_DIR/docker-compose.yml" << EOF
services:
  nginx:
    image: nginx:alpine
    ports:
      - "\${APP_PORT:-8080}:80"
    volumes:
      - ./nginx/nginx-prod.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - backend
    healthcheck:
      test: ["CMD", "nginx", "-t"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: always

  backend:
    build: ./backend
    environment:
      - APP_ENV=production
      - APP_SECRET=\${APP_SECRET}
      - MEDIA_ROOT=/media
      - DATABASE_URL=sqlite:///./data/mediascan.db
      - HOST=0.0.0.0
      - PORT=8000
      - ADMIN_USERNAME=\${ADMIN_USERNAME}
      - ADMIN_PASSWORD=\${ADMIN_PASSWORD}
    volumes:
      - ./backend:/app
      - ./frontend:/frontend
      - ./data:/app/data
      - ./static:/app/static
      - \${MEDIA_ROOT}:/media:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: always
EOF

# 构建并启动
echo ""
echo " 构建并启动 WhiteSnow..."
cd "$PROJECT_DIR"
docker compose build
docker compose up -d

# 等待服务启动
echo ""
echo " 等待服务启动..."
sleep 10

# 检查状态
echo ""
echo "=========================================="
echo " 部署完成！"
echo "=========================================="
echo ""
echo " 服务状态:"
docker compose ps
echo ""
echo " 访问地址:"
NAS_IP=$(hostname -I | awk '{print $1}')
echo "   http://${NAS_IP}:${APP_PORT}"
echo ""
echo " 默认管理员账号:"
echo "   用户名：admin"
echo "   密码：$(grep ADMIN_PASSWORD .env | cut -d'=' -f2)"
echo ""
echo " 下一步:"
echo "   1. 在浏览器打开 http://${NAS_IP}:${APP_PORT}"
echo "   2. 点击「扫描媒体库」扫描你的媒体"
echo "   3. 享受你的媒体库！"
echo ""
echo "=========================================="
