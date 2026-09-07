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
# 按需编辑 .env.dev，配置数据库、AI 服务密钥等
docker compose -f docker-compose.dev.yml up -d
```

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

SourceLens 有两种生产部署方式，每台主机选择一种：

- **Standalone 单实例**：使用 `install.sh` 安装和升级。
- **零停机蓝绿部署**：使用 `scripts/install.sh <tag>` 部署。

默认端口为 HTTP 10080 和 HTTPS 10443。

### 一键安装

使用一键安装器可完成 SourceLens 的安装和启动，并在完成后检查服务健康状态。

前置要求：

- Docker + Docker Compose V2（`docker compose`）

```bash
curl -fsSL https://raw.githubusercontent.com/oneprolabs/sourcelens/main/install.sh | \
  sudo bash
```

网络无法稳定访问 GitHub 时，使用中国分发通道：

```bash
curl -fsSL https://gitee.com/oneprolabs/sourcelens/raw/main/install.sh | \
  sudo bash -s -- --channel cn --download-source gitee
```

全新安装时，安装器会选择当前可用的最新 release tag。已有安装默认沿用当前版本；
如需升级或安装指定版本，增加 `--version <version>`。完整选项见
`install.sh --help`。

安装完成后：

- 主站：`http://<host>:10080`（HTTPS：`https://<host>:10443`）
- 配置文件：`<install-dir>/.env`
- 安装详情和初始管理员密码：`<install-dir>/install-info.env`

交互式安装或重复运行时，如果没有已启用的系统模型，安装器会在最终摘要前引导
配置。使用方向键选择模型服务商和模型，在显示星号掩码的输入框中填写 API Key 后，
安装器会先测试连接，再将配置保存为系统默认模型。重复运行安装器不会覆盖已有模型
配置。

使用 `--yes` 可跳过交互式模型配置。之后可前往
`http://<host>:10080/management/llm/config` 配置或管理模型。

如果 SourceLens 安装在 NAT 虚拟机中，需要先将宿主机 TCP 端口 `10080` 转发到
虚拟机 TCP 端口 `10080`，再从宿主机浏览器访问。

测试本地修改时，将包含已初始化子模块的完整仓库传到目标机器，然后运行：

```bash
sudo bash /tmp/sourcelens-source/install.sh \
  --source /tmp/sourcelens-source \
  --dir /opt/sourcelens-fixed-test
```

`--source` 会从该目录读取安装和部署文件，应用镜像仍然从镜像仓库拉取。使用
非默认安装目录时，安装器会创建隔离的测试环境，避免替换已有安装；如果 `10080`
已占用，安装器会提示选择其他端口。

默认安装目录为 `/opt/sourcelens`。使用新 tag 重复运行安装器即可原地升级，已有
`.env` 配置和应用数据会被保留。

如需零停机升级，请参阅
[`docs/blue-green-deployment.md`](docs/blue-green-deployment.md)。

## 技术栈

**后端**：Python · Django REST Framework · Celery · PostgreSQL  
**前端**：Vue 3 · Vite · Pinia · Vue Router · Tailwind CSS · vue-i18n  
**基础设施**：Docker · Nginx · Redis  

## 设计原则

每个 Django app 自包含（models、views、serializers、services、migrations、tests），app 之间通过 API 解耦。详见 [docs/DESIGN_PRINCIPLES.zh-CN.md](docs/DESIGN_PRINCIPLES.zh-CN.md)。
