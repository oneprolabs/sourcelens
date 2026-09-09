# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Behavioral Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with
project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial
tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes,
simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" -> "Write tests for invalid inputs, then make them pass"
- "Fix the bug" -> "Write a test that reproduces it, then make it pass"
- "Refactor X" -> "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```text
1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it
work") require constant clarification.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer
rewrites due to overcomplication, and clarifying questions come before
implementation rather than after mistakes.

## 项目概述

SourceLens 是一个保留基础架构的新项目，包含 Django REST API 后端和 Vue 3 前端。当前核心能力集中在用户认证、权限/角色管理、agentcore 管理台集成、任务调度基础设施和通知/LLM 管理能力。

核心开发目录：
- `backend/` — Django REST API、accounts、core 配置和 agentcore 集成
- `frontend/` — Vue 3 + Vite 前端和管理台页面

## 常用命令

### Docker 本地开发
```bash
cp env.sample .env.dev
docker-compose -f docker-compose.dev.yml up -d
# Web: http://localhost:8000
# API docs: http://localhost:8000/swagger/
# Celery monitor (Flower): http://localhost:5555
```

### 开发调试基本原则（容器内热加载）

本项目基于 **Django + Celery**，开发全程在容器内完成，依赖
`docker-compose.dev.yml`。代码以 volume 挂载方式进入容器
（`./backend:/opt/backend`），因此热加载行为按服务区分：

| 服务 | 容器名 | 代码改动后 | 说明 |
|---|---|---|---|
| `backend-api` | `sourcelens-api-dev` | **自动重载，无需重启** | 改动 Python 代码后 API 自动重启加载 |
| `backend-worker` | `sourcelens-worker-dev` | **必须手动重启** | Celery worker 不会热加载，任务代码改动后须重启 |
| `backend-scheduler` | `sourcelens-scheduler-dev` | 同 worker，按需重启 | Celery Beat，调度/任务代码改动后重启 |

**唯一需要重启 `backend-api` 的场景**：新增了 migrations 文件并需要作用于
数据库时，重启容器以执行迁移。

```bash
# 新增 migration 后，重启 api 使其生效（会执行 migrate）
docker restart sourcelens-api-dev

# worker / scheduler 代码改动后必须重启
docker restart sourcelens-worker-dev
docker restart sourcelens-scheduler-dev
```

> 速记：**改普通代码** → api 自动生效、worker 手动重启；
> **加 migration** → 额外重启 api。

### Docker 生产构建（蓝绿零停机）

生产使用 `scripts/install.sh` 做蓝绿部署，**不要**直接 `docker compose up -d`
（API/UI 是 blue/green profiled 服务，裸 `up` 不会启动任何一个颜色）。详见下方
「零停机部署 (Blue/Green)」章节。

```bash
# 首次安装 / 每次升级，同一条命令幂等：
curl -fsSL https://raw.githubusercontent.com/HyperBDR/sourcelens/<tag>/scripts/install.sh \
    -o install.sh && chmod +x install.sh && ./install.sh <tag>
# 默认端口: HTTP 10080, HTTPS 10443
```

### Python 开发（非 Docker）
```bash
pip install -e .[dev]
```

### 测试与代码质量
```bash
pytest                              # 运行所有测试
pytest path/to/test.py              # 单个测试文件
python backend/manage.py test        # Django test runner（部分测试）
black --check backend/              # 检查格式
isort --check backend/              # 检查 import 顺序
```

### Django 管理命令
```bash
python backend/manage.py migrate
python backend/manage.py register_periodic_tasks   # 注册所有定时任务
python backend/manage.py createsuperuser
```

### 前端
```bash
cd frontend
npm install
npm run dev          # 开发服务器
npm run build        # 生产构建
npm run lint         # ESLint
npm run test:e2e     # Playwright E2E
```

## 架构概览

### Django 应用结构（backend/）

每个 Django app 都应尽量自包含：拥有自己的 models、views、serializers、services、migrations、periodic_tasks 和 tests。跨 app 的回归测试放在 `backend/tests/`。

主要 App：
- **`accounts/`** — 用户认证、JWT、OAuth（Google）、Profile、Role、Permission
- **`core/`** — 项目配置、URL、Celery、分页、中间件和定时任务注册器

共享基础设施（`backend/core/`）：
- **`settings/`** — Django 配置分片（base.py、database.py、celery.py、rest.py、swagger.py、accounts.py、cache.py 等）
- **`urls.py`** — 根路由，mount accounts 和 agentcore API
- **`celery.py`** — Celery app 配置，含 autodiscover_tasks() 自动发现各 app 的 tasks.py
- **`periodic_registry.py`** — 定时任务注册器，所有 app 的 periodic_tasks 通过 `register_periodic_tasks()` 写入 django_celery_beat

### agentcore 包

agentcore 是独立维护并通过 Python 包索引安装的依赖，被当作 Django app 使用：

| 包 | 最低版本 | INSTALLED_APPS | 路由前缀 | 功能 |
|---|---|---|---|---|
| `agentcore-metering` | `0.2.0` | `agentcore_metering.adapters.django` | `/api/v1/admin/` | LLM 用量追踪 |
| `agentcore-task` | `0.1.0` | `agentcore_task.adapters.django` | `/api/v1/tasks/` | 统一任务管理 |
| `agentcore-notifier` | `0.1.0` | `agentcore_notifier.adapters.django` | `/api/v1/admin/notifications/` | 飞书通知 |

### 定时任务机制（Celery Beat）

- **Task 发现**：`core/celery.py` 调用 `app.autodiscover_tasks()`，自动加载所有 INSTALLED_APPS 中各 app 的 `tasks.py`
- **Periodic Task 注册**：不通过代码声明，而是在启动时由 `register_periodic_tasks` management command 发现各 app 的 `periodic_tasks.register_periodic_tasks()` 函数，写入 django_celery_beat 数据库。**现有记录不会被覆盖**，以保留运维人员在数据库中的自定义修改
- **容器入口**：`docker/entrypoint.sh` 在 gunicorn 容器中运行 `register_periodic_tasks` 一次；celery-beat 容器使用 DatabaseScheduler 从数据库读取调度

### API 层

- **REST Framework**：DRF + drf-spectacular（OpenAPI schema、Swagger UI、ReDoc）
- **认证**：dj-rest-auth (JWT) + django-allauth (OAuth Google) + allauth headless API（前后端分离场景）
- **国际化**：Django i18n，中文（zh-hans）和英文（en），通过 `LanguageCodeMappingMiddleware` 映射浏览器 Accept-Language
- **时区**：所有数据存储为 UTC，前端负责用户本地时区转换

### 前端（frontend/）

- Vue 3 + Vite + Composition API + `<script setup>`
- Pinia（状态管理）、Vue Router、vue-i18n、Tailwind CSS
- E2E：Playwright（`playwright.config.cjs`）
- 前端构建集成在根 `Dockerfile` 的 `frontend` target 中

## 编码规范（来自 Cursor rules）

- **响应语言**：所有解释用中文，代码注释用英文
- **GitHub 内容一律英文**：所有会出现在 GitHub 上的内容——commit message、
  PR 标题与正文、issue、PR/code review 评论等——**必须使用英文**；与用户的
  对话问答仍用中文。二者边界即"是否进入 GitHub"：进 GitHub 用英文，聊天用中文。
- **注释规则**：无行内注释，注释写在代码块上方；类和函数使用 docstring（triple quotes）
- **行宽**：每行最多 79 字符
- **Import 结构**：三段式（stdlib → third-party → local app），段内按字母排序，不混用
- **业务逻辑位置**：放在 models、serializers、services 中，views 只处理请求
- **Django/DRF**：优先使用 CBV（复杂逻辑）和 DRF 内置功能，不手写原始 SQL
- **调试用 print**：避免使用，用 logging 代替

## 部署编排选择（三选一）

三份 compose 各司其职：

| 文件 | 场景 | project 名 | 起停方式 | 镜像 | 零停机 |
|---|---|---|---|---|---|
| `docker-compose.dev.yml` | 开发 | `sourcelens-dev` | 裸 `docker compose -f … up -d` | 源码构建 + 热挂载 | 否 |
| `docker-compose.standalone.yml` | 生产 · 简单单实例 | `sourcelens` | 裸 `docker compose -f … up -d` | 拉发布镜像（无源码挂载） | 否 |
| `docker-compose.yml` | 生产 · 零停机 | `sourcelens` | `scripts/install.sh`（蓝绿） | 拉发布镜像 | 是 |

> **铁律：dev 与 prod 必须用不同的 compose project 名。** 每个 compose 文件都
> **显式**设顶层 `name:`（dev 为 `sourcelens-dev`，生产为 `sourcelens`），绝不
> 依赖"目录名"默认值——否则三者共享同一 project 命名空间 + 同名 `postgresql`/
> `redis` 服务键，在本目录起生产栈会**把 dev 容器顶掉重建**（反之亦然）。生产两
> 份共享 `sourcelens`（同一单例库数据），**只跑其一**。要隔离测试某个生产栈用
> `COMPOSE_PROJECT_NAME=sourcelens-verify`（覆盖文件里的 `name:`），不扰动其它。

- **只想 `docker compose up` 一把起、不要零停机** → `docker-compose.standalone.yml`
  （单 `backend-api` + 单文件 `docker/nginx/default.standalone.conf` 直连，无
  profiles、无 upstream 运行时状态）。部署就是 `pull` + `up -d`，会短暂重建
  backend-api（在途请求会断）。
- **要零停机** → 走下方蓝绿方案。注意 `docker-compose.yml` **不能**用裸
  `docker compose up -d` 起（API/UI 是 profiled 服务，且 nginx 依赖运行时
  `upstream.conf`），必须用 `install.sh`。

## 零停机部署 (Blue/Green)

单生产主机、无 k8s/Swarm。API/UI 各有 blue、green 两份，同一时刻只有一个颜色在
nginx 流量路径上。升级时先起空闲色、健康门控、原子切换 nginx（`nginx -s reload`，
不断连），观察一段时间后再退役旧色；回滚就是切回另一色。

> 完整的**可复用规范**（架构、逐项适配清单、正确性必须项清单、验证协议，供其他
> 项目移植）见 [`docs/blue-green-deployment.md`](docs/blue-green-deployment.md)。
> 本节是 sourcelens 的速查；改动部署脚本/nginx 前先过一遍那份的「正确性清单」。

### 命令

- **安装 / 升级**：`scripts/install.sh <tag>`（或 `--local [tag]` 用本地工作树
  构建、跳过远程拉取，便于对未提交改动做整链路测试）。一条命令幂等，首装与每次
  升级同路径。CI（`.github/workflows/build_and_deploy.yml`）在服务器上引导并运行
  它。
- **日常运维**：`scripts/sourcelensctl.sh {status|restart-workers|rollback}`。与
  install.sh 共享单飞锁与 `deploy-common.sh` 助手，但独立入口——"装新版本"和"操作
  已在跑的东西"风险面不同。`rollback` 不做 pull/build/migrate，只在目标色镜像仍在
  本地时可用。

### 关键约束

- **nginx 按整目录挂载**：`docker/nginx/conf.d/` 整个目录 bind-mount，**不是**单
  文件。切流用 `sed -i`/rename 原子改写 `upstream.conf`（换 inode）；单文件挂载会
  钉死在旧 inode，导致持续 502、旧色退役后变成 `host not found in upstream`。
- **运行时状态不提交**：`.active_color`、`.rollback_version` 与
  `docker/nginx/conf.d/upstream.conf` 由 install.sh 首次从 `upstream.conf.default`
  引导/切换时写入，均为运行时状态，`.gitignore` 已忽略，切勿提交或被 install 覆盖。
  （`.rollback_version` 记录被退役旧色的镜像版本，供 `sourcelensctl.sh rollback`
  用正确的旧镜像重建，而非 `:latest`。）
- **install.sh 是唯一支持的起停入口**：blue/green 服务之间不写 `depends_on`（
  profiled 服务被非 profiled 服务引用会破坏所有不带 `--profile` 的 compose 命令
  校验），起停顺序由脚本命令式掌控。裸 `docker compose up -d` 不受支持。
- **lensnode 经 nginx 寻址**：`LENSNODE_SERVER_URL=http://nginx:80`，始终打到
  active 色，切换时靠重连过渡（PR2 增加断连宽限期后，在途 run 不会被误判失败）。

### 迁移必须 expand/contract 安全

观察窗内，**旧色仍在对切换后的库表结构提供服务**。因此**同一发布**里既删/改列或
收紧约束、又上线不再使用它的代码，会在窗口内打挂旧色。此类变更必须拆成两个发布：
先加、双写/兼容，下个版本再删。

## 安全与配置

- 不提交 `.env`、secrets、证书；从 `env.sample` 复制创建
- 生产使用 PostgreSQL（推荐）、开发可用 SQLite
- `docker/nginx/certs/` 和云厂商配置发布前需审查
