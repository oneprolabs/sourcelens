# 数据源配置

数据源保存资源选择、同步策略和目标目录；Connection 提供可复用认证与允许范围。
共用的快照、lease 和凭证边界见 [Plugin 协议](../reference/plugin-protocol.md)，
同步目录、增量删除与转换进度见 [数据处理参考](../reference/datasource-processing.md)。

## GitHub 与 GitLab

向导第一步选择类型，第二步填写名称、访问方式和仓库。GitHub 使用
`datasource_config.repositories`，GitLab 使用 `datasource_config.projects`，
每个数据源支持 1–50 个资源。不预选示例仓库。

仓库可以从连接发现结果中选择，也可以手工输入资源路径或供应商 HTTPS 仓库 URL。
GitHub 使用 `owner/repo`；GitLab 支持嵌套命名空间。地址规范化和 Connection 范围
检查在服务端执行，拒绝凭证 URL、路径穿越及供应商不支持的地址形态。

### 匿名公开访问

公开 GitHub/GitLab 仓库可以选择无凭证公开访问，不必先创建用户 Connection。
GitLab 完整 URL 决定实例 origin；普通项目路径使用指定公开 endpoint 或 GitLab.com。
同一数据源不能混用实例 origin。

`POST /api/lens/admin/connections/validate-public-datasource/` 进行无持久化的匿名
预检。保存时创建或复用同供应商、同 origin 的系统 Connection；系统 Connection
不会出现在用户连接管理中，也不会扩展现有用户 Connection 的允许范围。
私有资源需要显式选择带凭证的 Connection。

已有无 secret version 的 GitHub Connection 也支持匿名访问。已存凭证被停用、
为空或不可读取时不能静默降级匿名；编辑时留空 Token 表示保留原凭证。
匿名 Connection 的 `*` 范围表示公开仓库，不提供账号仓库枚举。

匿名 Git 同步使用 `auth_scheme: none`，隔离 credential helper、netrc 与认证 Header。
匿名 GitLab 同步禁用 submodule；GitHub 在线代码搜索仍要求 Token。

### 仓库发现与校验

带凭证、允许范围为 `*` 的连接支持加载更多仓库/项目。分页结果去重并保留分支选项。
公开访问与受限范围连接不会通过“加载更多”枚举范围外资源。

发现仓库或分支不会批准数据源访问。用户选择的资源必须通过
`POST /api/lens/admin/connections/{uuid}/validate-datasource/` 校验；激活数据源时
服务端再次校验。禁用已有数据源不要求远程访问成功。

向导只复用配置未改变的成功结果。修改资源或连接使旧结果失效，迟到的发现或校验
响应不能覆盖新配置。

## 飞书

飞书 Plugin 提供数据源同步，不注册模型工具。Connection 保存 App ID 和只写
App Secret，endpoint 固定为 `https://open.feishu.cn`；认证校验确认可获得 tenant
access token，不枚举或保存文件夹/项目范围。

数据源 `resource_urls` 接受 HTTPS 飞书租户地址，可混用文件夹、文档、表格、幻灯片、
多维表格和知识库节点。用户无需手工判定类型；Provider 在保存时规范化、分类、去重
并保存运行时使用的 `resources`。

同步选项包括 `recursive`、`max_depth`、`incremental` 和 `delete_missing`。
每个配置地址保存前进行轻量读取检查；飞书应用本身的授权决定资源是否可读。
发现与下载使用有界并发预算并全局去重。App Secret 不进入数据源配置或执行快照。

自定义 endpoint、Lark 国际版和生产旧凭证自动迁移不属于当前飞书 Plugin 契约。
