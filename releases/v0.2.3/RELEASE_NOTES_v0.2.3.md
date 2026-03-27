## v0.2.3 - 货车详情 OCR 与分享定位修正

`v0.2.3` 重点修正了货车详情识别、分享目标定位、事件日志结构化存储，以及若干运行时稳定性问题，适合作为新的公开发布版本。

### 主要更新

- 重构货车详情 OCR
  - 按详情卡片中的三行分别识别玩家名称、等级、战力
  - 修正玩家名称、等级、战力互相污染的问题
  - 新增玩家等级日志输出
  - 玩家名称支持截断异常服务器编号，例如 `#19651` 会归一化为 `#1965`
- 优化货车战力识别
  - 战力行与玩家名称行彻底拆分
  - 修正战力被识别成极小异常值的问题
  - 进入战力核实时会输出完整的玩家名称、等级与战力
- 优化货车分享流程
  - 支持按配置自动分享到 `R4 & R5群` 或同盟群
  - 分享确认弹窗增加最终确认分享步骤
  - 分享列表定位增加动态识别与后备定位
  - 增加分享定位日志，标明使用动态识别还是固定比例
- 增强高价值货车记录与去重
  - 新增 `truck_plunder.YYYYMMDD.log`
  - 结构化记录玩家名称、等级、战力、UR碎片数、货车颜色、坐标等信息
  - 支持按历史记录自动跳过短时间内重复的货车
- 事件日志按类型拆分
  - `alliance_help.YYYYMMDD.log`
  - `dig_up_treasure.YYYYMMDD.log`
  - `truck_plunder.YYYYMMDD.log`
- OpenClaw 通知改为统一异步发送
  - 启动通知与事件通知不再阻塞主线程
  - 增加失败原因输出，便于排查 CLI 超时或配置错误
- 货车搜索与列表判定逻辑修正
  - 货车列表支持“至少 2 紫 2 金”的下限规则
  - 无效列表会先重试，再按当前列表继续遍历
  - 修正空列表、无效列表与刷新流程之间的异常跳转

### 热键说明

- `F12`：暂停 / 恢复实时监控
- `F5`：从基地重新定位车站并开始货车搜索
- `F6`：货车搜索中暂停 / 继续，或跳过当前目标货车
- `F2`：在鼠标当前位置开启 / 停止极速连点
  - 仅在 `F12` 暂停监控后可用
- `Ctrl-C`：退出程序

### 关键配置补充

- `truck.min_ur_shards`
- `truck.min_target_power_m`
- `truck.alert_enabled`
- `truck.alert_min_ur_shards`
- `truck.r4r5_share.*`
- `truck.alliance_share.*`
- `truck.share_wait_seconds`
- `truck.share_confirm_wait_seconds`
- `openclaw.enabled`
- `openclaw.startup_enabled`
- `openclaw.excavator_enabled`

### 日志

- 控制台最新日志：
  - `logs/LastWarBot_latest.log`
- 事件日志：
  - `logs/events/alliance_help.YYYYMMDD.log`
  - `logs/events/dig_up_treasure.YYYYMMDD.log`
  - `logs/events/truck_plunder.YYYYMMDD.log`

### 发布内容

本次发布建议包含：

- `LastWarBot.exe`
- `start.bat`
- `config.yaml`
- `README.md`
- `RELEASE_NOTES_v0.2.3.md`
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
  - `logs/LastWarBot_latest.log`
