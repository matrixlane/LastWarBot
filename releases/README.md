# Releases

发布相关内容统一收纳在此目录，并按版本分组。

`releases/` 是仓库中唯一保留的发布目录。
历史上本地曾使用过根目录下的 `release/` 作为临时打包目录，现已废弃；后续所有待发布目录、Release Notes 与压缩包都统一收纳到这里。

约定结构：

- `start.bat.template`：发布版 EXE 启动脚本模板，复制到各版本发布目录后命名为 `start.bat`
- `vX.Y.Z/LastWarBot-vX.Y.Z-win-x64.zip`：对应版本发布包
- `vX.Y.Z/RELEASE_NOTES_vX.Y.Z.md`：对应版本 Release Notes
- `vX.Y.Z/package/LastWarBot/`：解压后的待发布目录，用于检查和重新打包
