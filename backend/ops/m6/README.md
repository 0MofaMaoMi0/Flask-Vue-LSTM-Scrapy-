# M6 限定测试环境运维

仅适用于用户已授权、身份已确认的 Ubuntu VM；不是 Windows 或通用生产安装器。不要运行旧 `manage.py` 配置回退，不读取旧认证库，也不要把 guest 私有 env 文件放入源码或交付包。

`admin.py` 安装为 root 所有的固定操作入口，配置位于 guest `/etc/movieinsight-m6/`。`enable.sh` 是首次限定授权安装；后续 profile/只读身份升级还需要本机的已审核升级脚本。不要在其他机器盲目执行。

当前操作：

- `sudo -n /usr/local/sbin/mi2-m6-operations status`：非秘密状态；实际作用于 active-profile。
- `deploy`：只接收限额、无链接、白名单后缀的本地 release，保留旧版本，代码以低权限应用/迁移身份运行。
- `migrate`：专用迁移身份执行 `0001_auth → 0002_catalog → 0003_m6_activity`；冻结草案保持拒绝执行，正式 0002 从冻结权威生成 SQL。DDL 逐条记账，已有 ledger 不盲重试，没有自动 DROP。
- `check`：新 M6 测试，不导入旧 pytest 隔离配置。必须解析 JSON 的 `all_pass`；进程执行成功不代表测试通过。失败报告仅含安全检查名和位置，避免日志泄露参数。
- `start-app` / `restart-app`：管理 guest 回环 HTTP 服务；数据库重启时通过连接池预检与 readiness 恢复。
- `backup` / `restore-test`：受限逻辑备份、全新 Schema 恢复。逐表对比备份时点的行数/内容 checksum，跨 Schema FK 只指向新恢复 Schema。备份留在 guest root 私有目录，不打包 SQL。

真实来源只接受单独授权的静态副本路径与 SHA-256。`real-import-static` 不重采集、不修改原件、不迁移旧身份；每次都复用来源校验并核对目标。提交意图先持久化，未知提交保持阻断；独立只读账户须先取得导入命名锁，确认没有尚可能提交的原会话，再审计一致性快照。不能看到空表就认定回滚。

`app/m6_serve.py` 是 guest 测试服务入口。Windows 只运行 Vue 与受控 SSH HTTP 隧道，应用 DB 口令保留在 guest；会话 Cookie 使用 M6 独立名称及 `/api` 路径。

具体 active profile、账户、源副本授权、快照名、连接/凭据位置，以项目工作日志的当前交接及《M6-连接与凭据索引》为准。宿主 ACL 未授权，Navicat 管理权限保留，M7 不随这些命令解锁。
