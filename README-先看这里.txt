会议室预约系统 V2.5.1：当前入口
================================

一、先确认版本与发布边界
------------------------

当前产品代码位于 v2/，产品版本为 V2.5.1。V2 是全新安装产品，不导入、不读取、
不删除或覆盖 V1 的账号、笔录室、预约、配置和数据库。

macOS 13 及以上、Apple Silicon（arm64）使用 GitHub Release 中的 macOS
自托管包；这是正式分发渠道，当前 latest 正式二进制为 V2.5.1。Windows 10/11 64 位 x86-64（AMD64）安装包和累计
升级包仍是内部候选；Windows ARM64 与 32 位不在当前支持矩阵，
在普通用户实机验收与 Authenticode 签名完成前不得正式外发，且
formal_external_release_allowed=false。


二、用户电脑兼容性
------------------

交付包自带冻结运行时，普通用户无需另装 Python、Flask、Waitress 或 Node.js。
当前冻结服务运行时为 Python 3.13.14、Flask 3.1.3、Waitress 3.0.2；前端开发与构建
基线为 Node.js 22.17.1。Windows 版只支持可信的 Domain/Private 局域网，端口固定
为 8080；首次设置完成前仅监听 127.0.0.1。


三、当前操作入口
----------------

1. 产品与权限真值：v2/docs/PRODUCT-CONTRACT.md
2. API 真值：v2/docs/API-CONTRACT.md
3. 架构与运行：v2/docs/ARCHITECTURE.md
4. 当前发布门禁：v2/docs/RELEASE-CHECKLIST.md
5. Windows V2.5.1 全新安装实机验收：见发布清单；V2.5.0 指南仅为历史证据
6. Windows V2.1.0→V2.5.1 累计升级实机门禁：见发布清单；当前没有可执行的新任务书，
   不得改用旧 V2.3/V2.4.1 任务书替代
7. 完整版本记录：v2/CHANGELOG.md

不要从旧任务书、旧 run id、旧 SHA、旧候选文件名或仓库根目录的历史更新片段启动
当前发布与验收工作。历史文件只用于追溯当时的决策和证据。
