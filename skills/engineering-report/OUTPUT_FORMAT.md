# 整理内容与最终产出格式

这是 `engineering-report` 技能第五步引用的文件，抓完材料、准备写最终答案前必须读完这份文件的
全文，不能只读 `SKILL.md` 就凭印象产出。

## 一、整理内容——固定四段结构，人物维度

**按人分段**整理，每人固定四个部分。根据证据区分“未观察到”和“确认不存在”，不要将两者混用：

1. **姓名/账号**（小标题）
2. **完成的**：编号列表，每条一句话讲清楚做了什么/解决了什么问题，标来源和角色，如
   "完成 XX 兼容性修复 (GitHub · 实现)（PR #612）"。不要写"3 个 PR 已合并"这种产物计数。
3. **正在做的**：同样编号列出，跟“完成的”分开；没有相关记录时写“本次检索未观察到”。
4. **风险**：由模型结合具体事项、来源覆盖和时间证据判断。可以指出有明确证据的回退、阻塞或
   验证缺口，并给出事项编号；缺少测试记录时说明“未检索到验证记录”，不要直接断言没有测试。
   状态持续时间需要状态变更历史支持，不能用创建时间加当前状态推算。没有足够证据时如实说明。
5. **评语**：由模型判断证据能支持哪些质量、进展或工作量结论，并说明依据和必要的不确定性。
   不要求固定的正面或负面评价，不按记录条数或与其他人的数量对比自动评判产出。来源覆盖不全、
   未观察到完成事件，不足以独立证明某人没有完成工作；有充分证据的结论可以正常表达。

不要按来源分列（不要出现"GitHub 动态 / GitLab 动态 / Jira 动态"这样的分组），来源信息放在每
条事项里，不是内容的组织结构。某人本期确实没有任何可核实活动，如实说明，不要编造。

### 账号映射——只合并已确认的身份

工作区指引中的明确映射或可信的源账号关联可用于跨来源合并。仅名称、拼音前缀或数字后缀相似时，
保留原账号独立展示，可以标注待确认的关联，不替读者确认身份。不要自行翻译或编造真实姓名。

## 二、产出——静态 HTML 看板文件，不要输出 Markdown 正文

**最终交付物是一个自包含的 HTML 文件，不是聊天回答里的 Markdown 文本。** 原因：聊天消息经过
Markdown 渲染器和安全过滤（DOMPurify 白名单），`<style>`、`<script>`、内联 `style=` 属性统统
会被剥掉，长报告写成 Markdown 正文既没有样式也无法按人筛选，来回翻页体验差；HTML 文件作为
deliverable 交付则没有这个限制，且能在会话里预览、下载。

**产出方式**：

1. 写入前重新读取本文件，由模型结合真实证据组织 JSON 内容，并调用本技能的
   `scripts/render_report.py` 从下面的骨架渲染 HTML；保持基础 CSS 和布局，动态追加人员卡片和筛选规则。
   脚本不生成业务判断，不遗漏传入的人员，不把多个人合成一张卡片。
2. 将 HTML 写入 `{{SOURCELENS_DELIVERABLE_DIR}}`，复用运行时的自动附件交付。不要在聊天正文
   粘贴完整 HTML；用户明确要求纯文字时优先遵从用户要求。
3. 聊天回答正文保持简短：一两句话说明统计区间、数据覆盖情况（第一步产出的说明、抓取过程中遇到
   的截断/预算限制），以及"详细报告见下方文件"，不要在正文里重复每人的完整内容。

**修订方式**：复用已取证的账本和已有文件，只修改有问题的段落或事项；保留未变更的 HTML/CSS 和
其他人员内容。修改后重新保存附件，不要为改一句评语重新抓取来源或生成整份报告，不在修订说明中
重复整份旧稿。报告措辞由模型结合证据判断，不以特定关键词决定能否交付。

**筛选交互只能用 CSS，不能用 JS**：deliverable 在会话内预览走的是沙箱 iframe
（`sandbox=""`，未开 `allow-scripts`），`<script>` 不会执行；只有用户下载后用浏览器直接打开
才会跑脚本，不能依赖这条路径。用"隐藏 radio + label 当筛选按钮 + CSS 选择器控制显示/隐藏"这
个纯 CSS 方案，会话内预览和下载后打开都能正常筛选。

**HTML 模板骨架**（把 `<!-- ... -->` 部分替换成上面整理出的真实内容，每人一个 `<article>`，
CSS 结构照抄，只需要为每个人追加一组 radio + label + 对应的 `:checked` 隐藏规则）：

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title><!-- 统计周期，如"2026-08-19 昨日工作日报" --></title>
<style>
  :root {
    --bg:#F4F6F8; --surface:#FFFFFF; --ink:#16202B; --ink-soft:#4B5766;
    --ink-faint:#7C8896; --border:#DCE1E7; --accent:#2E6E86;
    --accent-soft:#E4EEF1; --success:#2E7D4F; --success-soft:#E4F2E9;
    --warn:#B4711E; --warn-soft:#FBEEDD; --flag:#B0472F; --flag-soft:#FBEAE5;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#121821; --surface:#1A222C; --ink:#E7ECF1; --ink-soft:#ABB6C2;
      --ink-faint:#78838F; --border:#2C3742; --accent:#74BBD0;
      --accent-soft:#1E323A; --success:#74CB98; --success-soft:#1C3226;
      --warn:#E4A75C; --warn-soft:#392C19; --flag:#E68C77; --flag-soft:#392019;
    }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",
      "Noto Sans SC",sans-serif; line-height:1.55; }
  .app { max-width:1320px; margin:0 auto; padding:24px 20px 48px; }
  input[name="filter"] { position:absolute; opacity:0; pointer-events:none; }
  .filters label { display:inline-block; padding:6px 12px; margin:0 6px 6px 0;
    border:1px solid var(--border); border-radius:999px; font-size:13px;
    cursor:pointer; color:var(--ink-soft); }
  .board { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr));
    gap:16px; }
  .card { background:var(--surface); border:1px solid var(--border);
    border-left:4px solid var(--ink-faint); border-radius:10px; padding:16px 18px; }
  .card.status-ok { border-left-color:var(--success); }
  .card.status-idle { border-left-color:var(--warn); }
  .sec-label { font-size:11px; text-transform:uppercase; letter-spacing:.06em;
    color:var(--ink-faint); font-weight:700; margin:10px 0 4px; }
  .sec ul { margin:0; padding-left:16px; } .sec li { font-size:13px; color:var(--ink-soft); }
  .risk p { color:var(--warn); font-size:13px; }
  .flag { display:inline-block; font-size:11px; padding:3px 8px; border-radius:6px;
    background:var(--flag-soft); color:var(--flag); border:1px dashed var(--flag); }
  /* 每人一组：radio 选中时，隐藏所有 data-name 不等于该人的卡片 */
  #f-all:checked ~ .board .card { display:block; }
  #f-<person-slug-1>:checked ~ .board .card:not([data-name="<person-slug-1>"]) { display:none; }
  #f-<person-slug-2>:checked ~ .board .card:not([data-name="<person-slug-2>"]) { display:none; }
  <!-- 每新增一人，追加一条上面这样的规则，person-slug 用不含空格的账号/姓名拼音 -->
</style>
</head>
<body>
<div class="app">
  <h1><!-- 统计周期标题 --></h1>
  <p><!-- 统计区间、数据覆盖说明，来自第一步/抓取过程 --></p>

  <input type="radio" name="filter" id="f-all" checked>
  <!-- 每人一个 radio，id 用 f-<person-slug> -->
  <nav class="filters">
    <label for="f-all">全部</label>
    <!-- 每人一个 label，for 对应上面的 radio id -->
  </nav>

  <main class="board">
    <!-- 每人一个 article，data-name 与对应 radio 的 person-slug 一致 -->
    <article class="card status-ok" data-name="<person-slug>">
      <h3><!-- 姓名/账号 --></h3>
      <!-- 如账号与映射表不完全一致： <span class="flag">账号与映射表不完全一致，建议核实</span> -->
      <div class="sec"><div class="sec-label">完成的</div><ul><!-- li 列表，或"无" --></ul></div>
      <div class="sec"><div class="sec-label">正在做的</div><ul><!-- li 列表，或"无" --></ul></div>
      <div class="sec risk"><div class="sec-label">风险</div><p><!-- 风险文本 --></p></div>
      <div class="sec"><div class="sec-label">评语</div><p><!-- 评语文本 --></p></div>
    </article>
  </main>
</div>
</body>
</html>
```

`status-ok`/`status-idle` 仅标记“本次检索是否观察到完成事件”，不代表人员绩效或实际工作量。
人数很多时卡片会很长，可以用原生 `<details>` 折叠
长列表（比如某人当日合并几十个 PR，按主题分组后用 `<details><summary>` 收起），不需要 JS。

不要把大段原始 API 输出、原始评论全文堆进 HTML 文件。最终交付是 HTML 文件
（自动附件交付），聊天正文只做简短说明，不要重复输出 Markdown 版的完整报告。
