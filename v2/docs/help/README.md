# 会议室预约系统 · 帮助中心

这是 V2.5 帮助中心的受版本控制内容源：首页、分类页、文章页、搜索结果页四层视图，问题式标题，本地搜索。
**完全离线、单文件、零外链资源、零遥测**——与产品立场一致。

## 目录结构

```
help-center/
├── build.mjs        # 构建器（无 npm 依赖，Node 直接运行）
├── test.mjs         # 布局与离线约束回归测试
├── content/         # 内容源：每篇文章一个 md 文件 + frontmatter
│   ├── quick-start/     快速上手
│   ├── booking/         预约与日历
│   ├── reminders/       提醒与通知
│   ├── handover/        工作交接
│   ├── account/         账号与登录
│   ├── data/            记录与统计
│   ├── admin/           管理员专区
│   ├── security/        安全与隐私
│   └── troubleshooting/ 故障排查
└── ../../frontend/dist/client/help/index.html # 生产构建产物
```

## 构建

```bash
node build.mjs --output ../../frontend/dist/client/help/index.html
```

产物为 `v2/frontend/dist/client/help/index.html` 单文件；它是生成物，不手工维护。构建器会校验：id 重复与格式（`^[a-z0-9-]+$`）、未知分类、related 死链、正文 Wiki Link 死链，
有任何警告时在写出成品前直接失败（退出码 1，不留下 index.html）。

## 图标与第三方许可

`build.mjs` 内嵌的 SVG 图标路径数据逐字取自 [Lucide Icons](https://lucide.dev)
（ISC License，© Lucide 作者），仅按产品线宽/圆角参数包装为 `<svg>` 壳。
这是帮助中心唯一的第三方素材来源；如更换或新增图标，必须同步在仓库根目录的
[`NOTICE`](../../../NOTICE) / [`THIRD_PARTY_NOTICES.md`](../../../THIRD_PARTY_NOTICES.md)
覆盖范围中复核此声明。

## 验证

```bash
node test.mjs
```

测试会重新构建成品，并校验四层页面骨架、文章与分类计数、无旧版常驻侧栏，以及无外链资源和网络请求。

## 写新文章

先阅读 [`CONTENT-STANDARD.md`](CONTENT-STANDARD.md)。它规定四种文章类型、员工与管理员内容边界、事实来源、16 分质量评分和发布前审校清单。`node build.mjs` 通过只代表结构有效，不代表文章已经达到发布质量。

在对应分类目录新建 md：

```markdown
---
id: my-article            # 全局唯一
title: 用户会问的那句话？   # 标题即问题
summary: 用一句话说明读者能从本文解决什么问题  # 分类列表摘要，24–50 字
category: booking          # 九个分类之一
roles: [employee, admin]   # 员工/管理员；两个都写显示"全员"
tags: [预约, 时段]          # 供搜索加权
related: [bk-create]       # 相关文章 id（构建器校验存在）
order: 100                 # 分类内排序，数字小靠前
updated: 2026-08-20
---
正文：Markdown 子集（##/### 标题、列表、表格、引用、**加粗**、`行内码`、[链接](#)、[[文章-id]]、[[文章-id|显示文字]]）
```

正文跨文章跳转使用稳定 ID：

```markdown
如果时段被抢先占用，请查看 [[bk-conflict|时段被占了怎么办]]。
```

构建后链接到 `#/a/bk-conflict`。目标不存在、ID 格式错误或显示文字为空都会阻止构建；行内代码中的 `[[bk-conflict]]` 保持原样，用于展示语法。不会根据 `tags` 自动给普通关键词加链接。

## 内容纪律

- 每篇文章中的界面文字（按钮、提示语）必须与实际系统逐字一致；数字、权限、状态变化和自动行为必须先由当前正式版本的运行结果与完整代码调用链验证，再用自动化测试固化。Contract 与 `USER-GUIDE.md` 用于发现设计偏差，不能单独作为文章事实来源。
- 标题写成用户的问题，不写成功能名。
- 不出现客户单位名称、真实人名；示例一律用"张女士/李静/笔录室 1"。

## 与产品的关系

- 登录后由侧栏底部问号入口直接进入完整离线帮助阅读器；它与未登录 `/help` 使用同一套文章、分类和搜索，不再维护第二个产品内帮助首页。
- 登录页提供低强调度入口；`/help` 和 `/help/` 无需登录即可读取完整离线帮助。
- `v2/VERSION` 是版本戳唯一来源；正式前端构建会同步生成帮助页。
- 帮助中心不调用业务 API、不读取浏览器存储、不发送搜索词，也不改变任何业务数据。
