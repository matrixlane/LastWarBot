## v0.2.4 - 启动自动化与发布目录整理

`v0.2.4` 重点补齐了游戏启动阶段的自动化流程，并统一了发布目录结构，适合作为新的公开发布版本。

### 主要更新

- 新增游戏自动启动能力
  - 当未发现 `LastWar.exe` 时，可自动搜索并启动游戏
  - 新配置项：`window.auto_launch_game`
  - 支持通过 `window.executable_path` 与 `window.search_roots` 缩小搜索范围
- 新增“仅对 Bot 启动的游戏生效”的自动 `F5`
  - 新配置项：`startup.auto_f5_after_bot_launch_enabled`
  - 仅当本次游戏由 Bot 自动拉起时，识别到基地后才会自动执行一次 `F5`
  - 不会对手动启动的游戏误触发
- 启动识别前增加界面加载等待
  - 新配置项：`startup.auto_f5_after_bot_launch_delay_seconds`
  - 等待发生在首次地图识别之前，避免游戏尚未完全加载时就进入识别与自动 `F5`
  - 启动阶段等待日志已收敛，避免轮询刷屏
- 货车刷新按钮识别稳定性修正
  - 修正全屏样例与纯蓝色按钮样例下的刷新按钮识别
  - OCR / 视觉相关测试已补强并回归通过
- 发布目录结构整理
  - 统一使用 `releases/` 保存 Release Notes、待发布目录与压缩包
  - 不再使用单独的 `release/` 目录，减少发布时的路径混淆

### 关键配置补充

- `window.auto_launch_game`
- `window.executable_path`
- `window.search_roots`
- `window.launch_retry_cooldown_seconds`
- `startup.auto_f5_after_bot_launch_enabled`
- `startup.auto_f5_after_bot_launch_delay_seconds`

### 发布内容

本次发布建议包含：

- `LastWarBot.exe`
- `start.bat`
- `config.yaml`
- `README.md`
- `RELEASE_NOTES_v0.2.4.md`
- `LICENSE`
- `images/`
- `sounds/`
- `logs/`

### 注意事项

- 请先完整解压后再运行，不要在压缩包内直接双击执行
- 若目标机器缺少系统运行库，请先安装：
  - [Microsoft Visual C++ Redistributable x64](https://aka.ms/vs/17/release/vc_redist.x64.exe)
- 本项目仍属于视觉自动化工具，不是直接读取游戏内部数据
- 不同分辨率、DPI 缩放、界面遮挡和动画效果仍可能影响识别稳定性
- 如遇异常，请优先查看：
  - `logs/Console_latest.log`
