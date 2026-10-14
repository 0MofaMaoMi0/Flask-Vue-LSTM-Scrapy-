# MySQL 数据目录迁移交接

## 当前状态

MySQL 8.0 的数据目录已从 F 盘迁移至 D 盘，并且服务已成功从新目录启动。

| 项目 | 当前值 |
| --- | --- |
| Windows 服务 | `MySQL80` |
| 服务状态 | `Running` |
| MySQL 程序目录 | `F:\\Navicat16_MySQL\\Database_filing\\MySQL\\MySQL Server 8.0\\bin` |
| 配置文件 | `F:\\Navicat16_MySQL\\Database_filing\\MySQL\\MySQL Server 8.0\\my.ini` |
| 当前数据目录 | `D:\\MySQL\\Data` |
| 配置项 | `datadir=D:/MySQL/Data` |
| MySQL 端口 | `3306` |

## 已完成操作

1. 停止 `MySQL80` 服务。
2. 使用管理员 PowerShell 以备份模式完整复制旧数据目录到 `D:\\MySQL\\Data`。
3. 复制结果：190 个文件、约 398.02 MB、失败数为 0。
4. 授予 MySQL 服务账户 `NT AUTHORITY\\NETWORK SERVICE` 对新数据目录的完全控制权限。
5. 为原配置创建备份，并将 `my.ini` 的 `datadir` 改为 `D:/MySQL/Data`。
6. 启动 `MySQL80` 服务并验证运行正常。

## 验证证据

- `Get-Service MySQL80` 返回 `Running`。
- `D:\\MySQL\\Data\\MEIFANNAO.err` 有本次启动记录，包含 InnoDB 初始化完成和 `ready for connections`。
- 该日志最后写入时间晚于切换时间；F 盘旧日志没有被新服务继续写入。
- `my.ini` 中的错误日志为相对路径 `log-error="MEIFANNAO.err"`，因此 D 盘日志的更新可佐证 MySQL 正在使用 D 盘数据目录。

## 数据库内容

主要业务数据库：

- `flask_douban_comment`
- `flask_douban_comment2`

可使用 Navicat 刷新本地 MySQL 连接后查看表和数据。连接服务为本机 MySQL，端口为 `3306`。

## 保留的回退材料

请暂时不要删除以下内容：

- 旧数据目录：`F:\\Navicat16_MySQL\\Database_filing\\MySQL\\MySQL Server 8.0\\Data`
- 原配置备份：`F:\\Navicat16_MySQL\\Database_filing\\MySQL\\MySQL Server 8.0\\my.ini.before-datadir-move`

建议在正常使用一段时间、确认 Navicat 中的库表与记录均正常后，再考虑清理 F 盘旧数据目录。配置备份文件体积很小，可长期保留。

## 回退步骤（仅在新目录出现问题时使用）

1. 以管理员身份停止服务：`Stop-Service MySQL80`
2. 将 `my.ini` 中的 `datadir` 改回：

   ```ini
   datadir=F:/Navicat16_MySQL/Database_filing/MySQL/MySQL Server 8.0/Data
   ```

   或使用 `my.ini.before-datadir-move` 恢复原配置。
3. 重新启动服务：`Start-Service MySQL80`
4. 使用 `Get-Service MySQL80` 和 Navicat 验证。

## 后续注意事项

- 不要在 MySQL 运行时手动移动、删除或编辑 `D:\\MySQL\\Data` 中的 `.ibd`、`ibdata1`、redo 日志等文件。
- 新建数据库、表及写入记录会默认保存至 `D:\\MySQL\\Data`。
- 数据库的日常查看、导出和备份建议通过 Navicat 或 MySQL 客户端完成。
