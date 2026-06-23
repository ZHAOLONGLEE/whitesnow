# CI/CD 自动部署

记录 WhiteSnow 推送自动同步到 QNAP NAS 的部署方案，方便以后排查问题或在新 NAS 上重新搭建。

## 📋 目录
1. [架构](#架构)
2. [涉及的文件](#涉及的文件)
3. [NAS 端搭建步骤](#nas-端搭建步骤)
4. [日常使用](#日常使用)
5. [已知坑](#已知坑)

---

## 🏗️ 架构

```
开发机 git push main
        │
        ▼
   GitHub Actions（云端，触发 workflow）
        │  （没有 SSH 进 NAS，没有暴露任何公网端口）
        ▼
   NAS 上的 self-hosted runner 容器（出站轮询 GitHub，主动认领任务）
        │
        ├─ rsync 把仓库代码同步到 /share/Container/whitesnow
        │   （排除 .env / data / static / docker-compose.yml，这些是 NAS 本地状态）
        │
        └─ docker compose up -d --build（在 /share/Container/whitesnow 里重建容器）
```

**为什么选 self-hosted runner，而不是 GitHub Actions 直接 SSH 进 NAS：**
NAS 在家庭局域网里，没有公网固定 IP，直接 SSH 方案需要把 SSH 端口转发暴露到公网，外加动态 DNS、专用部署密钥，安全性差且改动大。self-hosted runner 只需要 NAS 主动出站连 GitHub，不需要开放任何入站端口。

---

## 📁 涉及的文件

| 文件 | 作用 | 是否会被自动部署覆盖 |
|------|------|------|
| [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) | push 触发的部署流程 | 否（workflow 本身只在 GitHub 侧） |
| [docker-compose.runner.yml](../docker-compose.runner.yml) | 在 NAS 上跑 self-hosted runner 容器的配置 | 否（部署目录之外，单独维护） |
| [runner.env.example](../runner.env.example) | runner 需要的 GitHub PAT 配置模板 | 否 |
| NAS 上的 `/share/Container/whitesnow/docker-compose.yml` | 实际跑应用的 compose 配置（端口等 NAS 本地定制） | 否，已从 rsync 排除 |
| NAS 上的 `/share/Container/whitesnow/.env` `data/` `static/` | 密钥、数据库、封面图等本地状态 | 否，已从 rsync 排除 |

---

## ▶️ NAS 端搭建步骤

只需要做一次。

```bash
mkdir -p /share/Container/whitesnow-ci && cd /share/Container/whitesnow-ci

wget https://raw.githubusercontent.com/ZHAOLONGLEE/whitesnow/main/docker-compose.runner.yml
wget https://raw.githubusercontent.com/ZHAOLONGLEE/whitesnow/main/runner.env.example -O .env.runner
```

编辑 `.env.runner`（QNAP 默认 shell 没有 `nano`，用 heredoc 直接覆盖写入，不要用编辑器粘贴整段命令，否则容易把命令本身存进文件）：
```bash
cat > .env.runner << 'EOF'
GH_ACCESS_TOKEN=你的GitHub_PAT（classic token，勾 repo 权限）
EOF
```

启动：
```bash
docker compose -f docker-compose.runner.yml --env-file .env.runner up -d
docker compose -f docker-compose.runner.yml logs -f
```

看到 `Listening for Jobs` 即注册成功。去仓库 **Settings → Actions → Runners** 确认 `qnap-nas` 状态为 **Idle**。

---

## 🔄 日常使用

```bash
# 开发机
git add .
git commit -m "..."
git push origin main
```

push 之后几十秒内，NAS 会自动同步代码并重建容器。可以在仓库的 **Actions** 标签页看每次部署的状态和日志，也可以在 NAS 上看 runner 的实时日志：
```bash
docker compose -f /share/Container/whitesnow-ci/docker-compose.runner.yml logs -f
```

验证部署生效：
```bash
curl http://localhost:8888/health
```

---

## ⚠️ 已知坑

### 1. nginx 端口和 QNAP 系统服务冲突
仓库默认的 `docker-compose.yml` 把 nginx 写死映射到 `8081:80`，但 QNAP 自带的 `fcgi-pm` 系统服务本来就占着 `8081`，导致容器起不来（`bind: address already in use`）。NAS 上实际用的是 `8888`：
```bash
sudo sed -i 's/"8081:80"/"8888:80"/' /share/Container/whitesnow/docker-compose.yml
```
这个文件已经从自动部署的同步范围里排除了，改一次就会一直保留，不会被后续 push 覆盖。

### 2. 别用编辑器粘贴整段「`cat > file << EOF ...`」命令
在聊天里给的 heredoc 命令是要在**终端里执行**的，如果连同 `cat > ... << 'EOF'` 这行一起粘贴进 `vi` 之类的编辑器当正文保存，会把命令本身存成文件内容，导致 YAML 解析报错（`mapping values are not allowed`）。同时复制粘贴容易带入 Windows 换行符（`^M`/CRLF），也会导致 YAML 解析失败。如果遇到这种报错，最简单的修复是直接 `wget` 重新拉一份干净文件，而不是手动修。

### 3. GitHub PAT 安全
`docker-compose.runner.yml` 里的 `ACCESS_TOKEN` 用的是 classic PAT（`repo` 权限），仅用于 runner 自动注册/续期。**不要把 token 贴在聊天记录或任何会被记录的地方**，泄露后应立即在 GitHub Settings → Developer settings → Personal access tokens 里吊销重建。

---

*最后更新：2026-06-23*
