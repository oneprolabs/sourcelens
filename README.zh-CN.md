<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="frontend/public/brand/logo_with_text_dark.png">
  <img alt="SourceLens" src="frontend/public/brand/logo_with_text_transparent.png" width="320">
</picture>

中文 | [English](README.md)

**基于 Harness 的 Agentic RAG** — 无需 embedding，无需向量数据库，无需提前建索引

</div>

**SourceLens** 是一套基于 Harness 的 Agentic RAG（Agentic Retrieval-Augmented Generation）方案：底层由一套 AI 编程 agent harness 驱动（跟 Cursor、Claude Code、Codex 背后是同一类 harness，但并非直接集成这些产品本身），在沙箱环境中运行。它不需要提前把文件做 embedding 建成向量索引，而是直接把文档和代码交给 agent harness，由其在文件系统上按需读取、检索、推理——让任意一堆文档或代码都能直接拿来问问题。

![What is SourceLens](docs/images/what_is_sourcelens.png)

区别于向量嵌入或关键词索引，SourceLens 让 AI 编程 agent 在沙箱中直接读取、导航和推理文件系统。这意味着检索过程能够理解代码结构、跨文件关系和语义意图，而非仅停留在表层文本匹配。

## 项目背景

我们最早做 RAG 用的是 Dify、n8n 这类图形化编排工具。这类工具对使用者的专业要求很高，而真正的难点始终在前期：文档要先拆分、做 embedding，才能存入向量库。这一步准备工作要做好并不容易，投入了不少精力之后，召回的准确率却始终不太理想——回答经常不完整，有时候明明文档里写着答案，却还是会被漏掉。

差不多同一时期，我们在用 Cursor 做开发时注意到一件不一样的事：它完全没有做任何预训练或者预先建索引的动作，但在代码库上推理、回答问题的准确度却一直很稳。这就带来一个很自然的问题——既然如此，为什么不能把这套思路用在 RAG 上？

这就是 SourceLens 的由来：不走"先 embedding、再检索"这条传统路径，而是把文档和代码直接交给 AI 编程 agent harness——跟 Cursor、Claude Code、Codex 背后是同一套逻辑——让它直接读取、推理。实际用下来，我们发现答案的准确度、精确度和简练程度，都明显好过传统 RAG 方式的效果，这段经历也就变成了现在这个项目。

现在大多数团队搭建 RAG 知识库，走的基本都是这一类图形化编排工具的路径——Dify、n8n、Coze（扣子）、FastGPT 之类的工具，接上一个向量库。SourceLens 的核心目标不一样：把搭建一套能跑起来的 RAG 系统的成本尽量压到接近于零，同时不牺牲回答质量。

底层逻辑刻意保持简单：一个 Query 触发 agent 去检索，把找到的内容总结成阶段性答案；如果还不够，就再检索、再总结，如此循环，直到能够给出有把握的回答。未来会通过集成 Skills 和 MCP，在不改变这个核心循环的前提下，扩展 agent 能够触达的边界，不再局限于本地文件系统。

## 为什么选择 SourceLens

- **Agentic RAG，而非 embedding** — agent harness（跟 Cursor、Claude Code、Codex 背后是同一类 harness）直接读取并推理文件，无需向量数据库，无需提前建索引
- **沙箱隔离执行** — 所有 agent 操作在隔离环境中运行，安全处理任意代码仓库和文档
- **LLM 前后置编排** — 检索前后可配置 LLM 步骤，优化查询理解与答案合成
- **来源可追溯** — 每个答案精确关联到源文件路径和代码位置
- **任意格式，零准备** — Markdown、Word、PPT、图片、代码（py, js, ts, vue, go 等）都能直接用

## 使用场景

以下是我们内部目前在用的三个典型场景：

### 场景一：文档 RAG —— 无需提前 embedding

把文档丢给 SourceLens，无需任何提前的 embedding 工作，直接就能开始提问，支持：

- 来自 ViewPress 等线上文档平台的 Markdown
- Word 文档
- PPT 文档
- 图片中的内容

### 场景二：截图驱动的代码深度洞察

发现一个报错？直接截图错误内容，agent harness 会顺着截图深入代码源头做筛查，而不只是做字符串层面的错误匹配。

### 场景三：公司级 Skills 的通用对话模式

我们把内部工程知识沉淀成公司级 Skills，任何人都能基于它发现和定位问题，统一收敛成一种通用对话模式：

- **无需安装** — 不需要在本地工具里提前装这个 Skills
- **在线获取答案** — 直接在对话里提问，答案当场拿走
- **生成与下载** — 同一个对话还能帮你生成文件并下载

> 另一个测试中发现的有趣场景：把同样的深度洞察能力用在小说等长文本内容上，也是一种很有意思的探索和检索方式。

## 架构总览

```
sourcelens/
├── backend/                    # Django REST API
│   ├── core/                   # 项目配置（settings/、urls.py、celery.py）
│   ├── accounts/               # 用户认证、权限与角色管理
│   └── agentcore/              # Git 子模块
│       ├── agentcore-metering/  # LLM 用量追踪  → /api/v1/admin/
│       ├── agentcore-task/      # 统一任务管理   → /api/v1/tasks/
│       └── agentcore-notifier/  # 通知服务       → /api/v1/admin/notifications/
├── frontend/                   # Vue 3（Vite + Pinia + Tailwind + vue-i18n）
└── docs/                       # 设计文档
```

## 快速上手

### 1. 拉取子模块

```bash
git submodule update --init --recursive
```

### 2. Docker 本地开发

> **前置要求**：必须使用 Docker Compose **V2**（`docker compose`）。开发栈依赖
> Compose V2 特性——顶层 `name` 字段（dev/prod 项目隔离）、`pull_policy` 和
> `depends_on.condition` 健康门控——旧版 Docker Compose v1（`docker-compose`，
> 如 1.29.x）会在 `up -d` 时因 schema 校验报错而拒绝。可用
> `docker compose version` 确认版本。

```bash
cp env.sample .env.dev
# 按需编辑 .env.dev，配置数据库、AI 服务密钥和已批准的 LENSNODE_TOKEN
# LENSNODE_TOKEN 必须与管理后台中的 LensNode 记录保持一致
docker compose -f docker-compose.dev.yml up -d
```

如果在管理后台重置了 LensNode Token，请同步更新 `.env.dev` 中的
`LENSNODE_TOKEN`，并重新创建开发服务，使 LensNode 能够重新连接。

### 3. 访问服务

| 服务 | 地址 |
|---|---|
| Web UI | http://localhost:8000 |
| API 文档 | http://localhost:8000/swagger/ |
| 管理后台 | http://localhost:8000/admin/ |
| Flower | http://localhost:5555 |

### 4. 常用命令

```bash
# 后端测试
pytest
pytest path/to/test.py

# Django 管理
python backend/manage.py migrate
python backend/manage.py register_periodic_tasks
python backend/manage.py createsuperuser

# 代码质量
black --check backend/
isort --check backend/

# 前端
cd frontend && npm install
npm run dev          # → http://localhost:5173
npm run build
npm run lint
npm run test:e2e     # Playwright E2E
```

## Agentcore 子模块

| 子模块 | Django App | URL 前缀 |
|---|---|---|
| `agentcore-metering` | `agentcore_metering.adapters.django` | `/api/v1/admin/` |
| `agentcore-task` | `agentcore_task.adapters.django` | `/api/v1/tasks/` |
| `agentcore-notifier` | `agentcore_notifier.adapters.django` | `/api/v1/admin/notifications/` |

本地可编辑安装：

```bash
for d in backend/agentcore/*/; do
  [ -f "${d}pyproject.toml" ] && pip install -e "$d"
done
```

## Celery 任务机制

- **任务发现**：`core/celery.py` 通过 `autodiscover_tasks()` 自动加载各 app 的 `tasks.py`
- **定时任务**：通过 `register_periodic_tasks` 写入 `django_celery_beat`，现有记录不会被覆盖
- **启动顺序**：`wait_for_db` → `migrate` → `register_periodic_tasks` → 启动服务

## 生产部署

SourceLens 有两种生产形态——每台主机**只选一种**（两者共享 `sourcelens` compose 项目名）：

- **Standalone 单实例**（一个 backend-api、一个 frontend，无蓝绿切换）。
  一键安装器：从 tag 拉取 release 配置文件、生成带随机密钥的 `.env`、拉取镜像、
  启动栈并做健康检查。GitHub 不可达时用 `-c cn`，镜像改从阿里云 ACR 拉取、
  配置文件从 Gitee 下载。

  ```bash
  curl -fsSL https://raw.githubusercontent.com/oneprolabs/sourcelens/<tag>/install.sh \
      -o install.sh && chmod +x install.sh && sudo ./install.sh <tag>
  ```

- **零停机蓝绿**（docker-compose.yml）：`scripts/install.sh <tag>`。

默认端口：HTTP 10080, HTTPS 10443（可通过 `NGINX_HTTP_PORT`、`NGINX_HTTPS_PORT` 调整）。

### 自动化部署（standalone 安装器）

standalone 栈可用一条命令完成安装与升级：从仓库 tag 下载 release 配置文件
（`docker-compose.standalone.yml`、nginx/postgres 配置、`env.sample`），生成带
随机密钥的生产 `.env`，拉取容器镜像，启动栈并做健康检查。

```bash
curl -fsSL https://raw.githubusercontent.com/oneprolabs/sourcelens/<tag>/install.sh \
    -o install.sh && chmod +x install.sh && sudo ./install.sh <tag>
```

- **前置要求**：Docker + Docker Compose **V2**（`docker compose`）。旧版
  Compose v1 会被拒绝——compose 文件使用了 V2 专属特性。
- **幂等升级**：用更新的 tag 重跑同一条命令即可原地升级。已有 `.env` 绝不
  覆盖，只会把已知的不安全占位值（`change-me`、`postgres`、`adminpassword`、
  `change-me-lensnode-token`）替换为随机密钥。初始管理员用户名/密码会在结束
  时打印并保存在 `install-info.env`。
- **通道**：默认 release 文件来自 GitHub、应用镜像来自 Docker Hub
  （`oneprolabs/*`）。GitHub 不可达时加 `-c cn`，release 文件改从 Gitee 下载、
  应用镜像改从阿里云 ACR（`registry.cn-beijing.aliyuncs.com/oneprolabs`）拉取。
  基础设施镜像（postgres/redis/nginx）始终来自 Docker Hub。
- **端口**：HTTP 默认 10080、HTTPS 默认 10443。检测到端口被占用时会自动
  使用下一个空闲端口。

常用选项：

| 选项 | 说明 |
|---|---|
| `-d, --dir DIR` | 安装目录（默认 `/opt/sourcelens`） |
| `-p, --port PORT` | HTTP 端口（默认 10080） |
| `-v, --version VER` | 发布版本（默认：最新 tag） |
| `-c, --channel github\|cn` | 分发通道（默认：自动探测） |
| `--download-source github\|gitee` | release 文件来源；同时决定镜像仓库 |
| `--source DIR` | 使用本地仓库代码而非下载（测试/离线） |
| `--domain HOST` | 公网主机名/IP（默认：自动探测） |
| `-y, --yes` | 非交互：接受默认值，不提问 |

完整选项及环境变量覆盖（`SOURCELENS_INSTALL_DIR`、`SOURCELENS_HTTP_PORT`、
`SOURCELENS_HTTPS_PORT`、`SOURCELENS_VERSION`、`SOURCELENS_REGISTRY`、
`SOURCELENS_DOMAIN` 等）见 `install.sh --help`。

> **Cloudflare Turnstile**：`env.sample` 保持 `TURNSTILE_ENABLED=true`，生产
> 环境要求配置真实的 `TURNSTILE_SECRET_KEY`——否则后端拒绝启动（这是刻意的
> fail-fast 守卫）。一键安装器无法生成真实密钥，因此在全新安装时会把
> `TURNSTILE_ENABLED` 设为 `false`，让登录在无验证组件下可用。之后要启用
> Turnstile，请在 `.env` 中配置真实密钥（及前端 site key），再把
> `TURNSTILE_ENABLED` 翻回 `true`。

### 容量与并发调优

生产面向多用户，按负载在服务器 `.env` 中调整这些值（CI 不会覆盖 `.env`，跨部署保留）：

| 变量 | 作用 | 默认 | 调优建议 |
|---|---|---|---|
| `LENSNODE_MAX_CONCURRENT_RUNS` | 单个 LensNode 的并发问答数 | `1` | **真正的吞吐上限——务必调大。** 节点满时新 run 会停在 `Queued`（每 5s 重试，最长 120s）。设为 ≥ 最繁忙助手的 `max_concurrency`，并按内存（每个 deep-agent 回答约数百 MB）与上游 LLM 限流量力而行。 |
| `CELERY_CONCURRENCY` | Celery worker 进程数 | CPU 核数 | 很少是瓶颈：worker 任务只是把活派发给 LensNode（重活在 LensNode 上跑）。适度上调只为留余量。 |
| `max_concurrency`（每助手，DB） | 单个助手的并发 run 数 | `5` | 按助手限流；系统级上限是 `LENSNODE_MAX_CONCURRENT_RUNS`。 |

- API 实为**单个 Daphne ASGI 进程**（只占 1 核）。async 能抗大量并发连接，但要用更多核需多开 ASGI worker/副本——不是 `.env` 能搞定的。
- 服务器上用 **Docker Compose v2**（`docker compose`）。旧版 v1（`docker-compose`）会因部署未下发的 `build:` 上下文而中止 `up -d`。
- 改完 `.env` 需重建而非重启（`docker restart` 不会重读 env 文件）：

  ```bash
  APP_VERSION=<version> docker compose up -d --force-recreate --no-deps lensnode backend-worker
  ```

## 技术栈

**后端**：Python · Django REST Framework · Celery · PostgreSQL  
**前端**：Vue 3 · Vite · Pinia · Vue Router · Tailwind CSS · vue-i18n  
**基础设施**：Docker · Nginx · Redis  

## 设计原则

每个 Django app 自包含（models、views、serializers、services、migrations、tests），app 之间通过 API 解耦。详见 [docs/DESIGN_PRINCIPLES.zh-CN.md](docs/DESIGN_PRINCIPLES.zh-CN.md)。
