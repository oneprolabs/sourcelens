# SourceLens 本地开发环境安全审计（2026-11-19）

`localhost:8000` 所在开发栈不应连接不可信网络，也不应使用真实租户数据。最紧急的是 Flower 无需登录、开发服务发布在宿主机全地址，以及 Django 签名密钥未通过安全检查。多租户发布前还要解决两个应用 API 无法覆盖的边界：Nginx 直出共享存储、LensNode 在共享容器内执行命令。

范围：当前多租户工作树和正在运行的开发容器；发布配置只做代码审查，未访问生产。未尝试登录、读取业务文件、跨租户访问或发送变更请求。

| 优先级 | 问题 | 证据与影响 | 修复建议 |
| --- | --- | --- | --- |
| P0 · 已确认 | Flower 匿名开放，Web 与 PostgreSQL 对宿主机全地址监听 | `8000/flower/` 和 `5555` 首页匿名均返回 `200`；页面呈现 Workers、Tasks、Broker 导航。`8000`、`5555`、`5432` 均未限制为回环地址。外部实际可达性取决于宿主机防火墙和网络；未验证数据库登录或 Flower 运维动作。 | Web 默认绑定回环地址；移除数据库宿主机端口和 Flower 的两条入口。确需 Flower 时仅在管理网络开放并要求身份认证。参见 `docker-compose.dev.yml`、`docker/nginx/default.dev.conf`。 |
| P0 · 已确认配置 | Django 签名密钥未通过部署检查，并被 JWT 共用 | 容器内 `check --deploy` 报 `security.W009`；仅比对配置，确认 Simple JWT 使用同一密钥，未输出密钥或验证令牌伪造。两类签名共用密钥扩大了密钥泄露的影响。 | 生成每个环境独立的高熵密钥；为 JWT 独立配置签名密钥。轮换前梳理现有会话、令牌及其他签名用途，安排失效和重新登录。参见 `backend/core/settings/base.py`、`backend/core/settings/rest.py`。 |
| P0 · 发布阻断 | 共享存储由 Nginx 直接公开提供 | 开发、蓝绿、单实例三份 Nginx 配置都直接映射整个共享存储并允许公开缓存；当前应用的 Skill 包和上传图片默认位于映射目录下。Skill 下载 API 有对象鉴权，Nginx 静态路径不经过它。已确认配置重叠，未读取真实文件，未确认有租户数据泄露。文档附件与交付物默认另用存储。 | 将公开静态资源与私有文件分开；私有文件先校验资源和租户归属，再用内部转发或短期签名链接交付。复核已缓存内容及现有链接。参见 `docker/nginx`、`backend/core/settings/base.py`、`backend/lens/skill_packages.py`。 |
| P0 · 发布阻断 | LensNode 没有每次运行的命令执行沙箱 | 本地 LensNode 没有覆盖执行模式，使用默认 `trusted_container`；本地进程为 UID 0。允许命令执行的运行使用节点内本地 Shell，继承进程环境，且开发节点与 API、数据库同网。共享节点不能作为互不信任租户的执行隔离；未运行任何验证命令来访问其他租户资源。 | 不需要命令的助手使用文件后端；需要执行的任务放入按租户或按次隔离的执行环境，使用非 root、最小挂载、最少凭据和受控出网。参见 `lensnode/lensnode/config.py`、`lensnode/lensnode/agent_runtime/runtime.py`。 |
| P1 · 已确认配置 | 开发 worker 挂载可写 Docker socket | Docker 检查确认 worker 可写访问 Docker socket；一旦 worker 进程被控制，影响可能扩大到 Docker 守护进程可管理的其他容器和挂载。未发现从 Web 到 worker 的已验证攻击链。 | 默认移除 socket；确实需要 Docker 的任务搬到隔离执行器，只开放必需操作。参见 `docker-compose.dev.yml`。 |
| P1 · 发布前复核 | 历史 staff 自动提升为平台超级管理员；Django 管理站点独立于应用租户权限 | 迁移 `0009_multitenancy` 把旧 `is_staff` 或 `is_superuser` 映射为平台 `super_admin`。Django 管理登录页可达，注册了租户、会话、运行等模型；本轮只确认登录门禁与注册配置，未验证任何历史账号能读取数据。 | 上线前对旧 staff 做白名单核对，权限迁移默认最小化；Django 管理站点仅限受控管理入口。参见 `backend/accounts/migrations/0009_multitenancy.py`、`backend/accounts/admin.py`、`backend/lens/admin.py`。 |
| P2 · 已确认 | 运维信息匿名可读；Web 首页缺少部分安全响应头 | Swagger、ReDoc、OpenAPI 匿名 `200`，Schema 含管理接口定义；Celery 健康接口公开队列和调度状态。首页响应无 CSP、防嵌入策略或 `nosniff`，部分 Django API 已有防嵌入与 `nosniff`。当前开发实例为 HTTP，Django 接受通配 Host。 | 公开健康检查只保留存活状态；详细诊断和内部文档限管理网络。收紧 Host 名单，在反向代理统一补安全头，并对共享或远程入口强制 HTTPS。参见 `backend/core/swagger.py`、`backend/core/urls.py`、`backend/core/settings/base.py`。 |

浏览器：`ego-browser` 任务空间 **38**，核对普通登录页、Django 管理登录页及匿名 Flower 首页。匿名个人资料、会话列表、管理数据源列表及平台租户列表均返回 `401`。这覆盖了所抽查的登录门禁；已登录身份间的跨租户隔离、登录和邮件限流、依赖漏洞未在本轮验证。
