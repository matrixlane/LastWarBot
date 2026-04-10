# LastWarBot

`LastWarBot` 是一个面向 Windows 的《Last War: Survival》视觉自动化辅助工具。

当前版本重点能力（`v0.2.3`）：

- 自动等待并检测 `LastWar.exe`
- 自动激活游戏窗口
- 自动识别并点击 Alliance Help
- 新增 `DigUpTreasure` 自动执行链路，并支持 `OpenClaw` 通知
- 玩家信息识别：等级、体力、粮食、铁矿、金币、战力、钻石
- 从基地进入车站，搜索货车，识别 `UR碎片`
- 货车详情 OCR 拆分识别玩家名称、等级、战力
- 根据 `UR碎片` 与战力条件筛选目标货车，并记录高价值货车日志
- 可选自动分享目标货车到 `R4 & R5` 或同盟群
- 自动跳过 1 小时内重复命中的同一目标货车
- 适配不同窗口尺寸与 DPI 缩放环境

## 运行环境

- Windows 11
- 《Last War: Survival》PC 版
- Python `3.13`
- `PaddleOCR + PaddlePaddle`

## 目录说明

- `lastwar_bot/`：主程序源码
- `images/templates/`：模板图像
- `sounds/`：提示音
- `releases/`：发布归档目录，按版本收纳发布包、Release Notes 与发布目录
- `releases/start.bat.template`：发布版 EXE 启动脚本模板
- `logs/events/`：事件日志
- `logs/Console_latest.log`：最近一次运行的控制台日志
- `config.yaml`：主配置文件
- `start.bat`：开发环境启动脚本（Python 方式）
- `release_start.bat`：发布版 EXE 启动脚本
- `run_lastwar_bot.py`：EXE 打包入口
- `LastWarBot.spec`：PyInstaller 打包配置

## 快速开始

### Python 方式

```powershell
pip install -e .
python -m lastwar_bot --config config.yaml
```

或直接运行：

```powershell
start.bat
```

### EXE 方式

发布版请保留完整目录结构后运行：

- `LastWarBot.exe`
- `start.bat`

其中发布目录里的 `start.bat` 建议由 `releases/start.bat.template` 复制得到。不要单独移动 `LastWarBot.exe`。

## 当前行为说明

### 窗口与分辨率

- 默认不再强制把游戏窗口改成固定 `1920x1080`
- 当前要求最小客户区不低于 `1024x728`
- 识别逻辑会根据当前客户区尺寸动态缩放
- 货车界面会先识别中间浮层，再在浮层内部识别货车、刷新按钮、分享按钮等元素

### 日志

- 控制台日志会同步保存到 `logs/Console_latest.log`
- 每次启动会覆盖上一次的 `Console_latest.log`
- 事件日志按类型拆分保存：
- `logs/events/alliance_help.YYYYMMDD.log`
- `logs/events/dig_up_treasure.YYYYMMDD.log`
- `logs/events/truck_plunder.YYYYMMDD.log`

### 热键

- `F12`：暂停 / 恢复实时监控
- `F5`：从基地重新定位车站并开始货车搜索
- `F6`：货车搜索中用于暂停 / 继续，或跳过当前目标货车
- `F2`：在鼠标当前位置开启 / 停止极速连点
- `Ctrl-C`：退出程序

### F2 连点行为

- 按下 `F2` 时，不再要求你先手动按 `F12`
- `F2` 启动连点时，会临时禁用 `F12`，并自动记录当时的实时监控状态
- 再次按下 `F2` 停止连点时，会自动恢复到启动连点前的 `F12` 状态
- 但若当前正在执行抢挖掘机自动化，按下 `F12` 会立刻中断该流程，并停止相关连点

### `v0.2.3` 货车行为补充

- 进入货车详情后，会分别识别玩家名称、等级、战力，避免三者互相污染
- 只有 `UR碎片` 达到 `truck.min_ur_shards` 后，才继续核实玩家战力
- 若已命中同一目标货车并在近 1 小时内记录过，会按历史日志自动跳过
- 货车列表默认要求满足“至少 2 紫 2 金”；连续重试失败后，才按当前列表继续搜索
- 分享目标会优先尝试动态定位；失败时再退回固定比例定位，并输出定位日志
- 自动分享会执行最终确认分享步骤

## 货车搜索流程

1. 在基地界面按下 `F5`
2. 程序缩小地图并查找车站图标
3. 若车站图标置信度过低，会自动平移地图重试
4. 进入货车界面后，先识别中间货车浮层
5. 在浮层内识别货车列表
6. 逐辆进入详情页，先识别 `UR碎片`
7. 只有当 `UR碎片` 数量达到搜索阈值时，才进行战力核实
8. 若满足搜索条件：
   - 会先根据 `truck.alert_enabled` 与 `truck.alert_min_ur_shards` 决定是否提醒
   - 若命中自动分享，则先按优先级判断分享目标
   - 先判断 `r4r5_share`
   - 未命中时再判断 `alliance_share`
   - 分享目标会先尝试动态识别，必要时退回固定比例定位
   - 同盟群不依赖文字识别，而是点击分享弹窗第二行
   - 命中后会点击确认分享弹窗中的 `分享`
   - 分享成功后继续搜索下一辆
   - 若未命中分享但已命中提醒阈值，则自动停下等待人工处理
   - 若提醒与分享都未命中，则自动跳过当前货车继续搜索
9. 若当前页没有目标货车，则自动刷新继续搜索
10. 一轮搜索结束后，会点击货车浮层边界外区域，退出回基地并恢复 `F12` 对应的实时监控状态
11. 若 `truck.restart_refresh_cycle_enabled: true`，则会在 `truck.restart_refresh_cycle_interval_minutes` 到时后自动执行 `F5`，重新进入车站并开始新一轮搜索

## 关键配置

### `window`

- `process_name`：进程名，默认 `LastWar.exe`
- `title_contains`：窗口标题关键字
- `min_client_width` / `min_client_height`：最小客户区要求
- `resize_enabled`：是否自动调整窗口大小
- `force_foreground_each_cycle`：每轮是否强制激活窗口

### `player_info`

- `enabled`：玩家信息识别开关
- `interval_seconds`：玩家信息识别周期
- `language`：识别语言
- `use_gpu`：是否启用 GPU

### `startup`

- `openclaw_message_enabled`：启动时是否发送 OpenClaw 通知

### `alliance_help`

- `click_cooldown_seconds`：Alliance Help 连续点击冷却
- `sound_enabled`：是否播放 Alliance Help 提示音

### `dig_up_treasure`

- `alert_cooldown_seconds`：DigUpTreasure 连续提醒冷却
- `sound_enabled`：是否播放 DigUpTreasure 提示音
- `openclaw_message_enabled`：是否发送 OpenClaw DigUpTreasure 通知
- `auto_execute_enabled`：检测到挖掘机后，是否自动执行“点地图挖掘机 -> 点聊天里的‘挖掘宝藏’分享框 -> 进现场点挖掘机和绿色挖掘按钮 -> 选队 -> 出征后立刻启动连点 -> 回基地”整条流程
- `auto_execute_cooldown_seconds`：自动执行的最小重试间隔
- `click_settle_seconds`：每次关键点击后的界面稳定等待
- `panel_timeout_seconds`：等待挖掘相关弹窗 / 按钮出现的超时时间
- `travel_buffer_seconds`：小队出征倒计时结束后额外追加的缓冲秒数
- `countdown_poll_interval_seconds`：挖掘倒计时轮询间隔
- `finish_wait_seconds`：挖掘完成后，收尾前额外等待时间
- `max_task_seconds`：单次挖掘自动化的最长执行时间
- 挖掘完成判定默认会同时参考倒计时与挖掘图标；只要挖掘图标仍在，流程不会因为 OCR 短暂读错而提前收尾

### `truck`

- `min_target_power_m`：允许的货车战力上限，单位百万
- `min_ur_shards`：货车搜索最小 `UR碎片` 阈值；满足后即使不提醒、不分享也会停下
- `alert_enabled`：满足货车搜索条件时是否提醒
- `alert_min_ur_shards`：触发高价值货车提醒所需的最少 `UR碎片`
- `r4r5_share.enabled`：是否开启 `R4 & R5` 自动分享
- `r4r5_share.min_ur_shards`：`R4 & R5` 自动分享所需的最少 `UR碎片`
- `alliance_share.enabled`：是否开启同盟群自动分享
- `alliance_share.min_ur_shards`：同盟群自动分享所需的最少 `UR碎片`
- 当两个分享规则都开启时，始终优先匹配 `R4 & R5`
- `share_wait_seconds`：点击分享后的等待时间
- `share_confirm_wait_seconds`：点击目标群与确认分享后的等待时间
- `max_refresh_attempts`：最多刷新次数
- `restart_refresh_cycle_enabled`：一轮货车搜索结束后，是否在基地暂停等待并自动执行下一次 `F5`
- `restart_refresh_cycle_interval_minutes`：自动执行下一次 `F5` 之前的等待分钟数
- `inspection_wait_seconds`：点击货车详情后的等待时间
- `refresh_wait_seconds`：刷新后的等待时间
- `enter_wait_seconds`：首次进入货车界面后的等待时间
- `enter_retry_count`：货车列表重试次数
- `sample_attempts`：货车列表单轮采样次数
- `sample_interval_seconds`：货车列表采样间隔
- `ur_shard_confirm_interval_seconds`：`UR碎片` 二次复核等待时间
- `empty_result_retry_rounds`：空结果延迟重试轮数
- `refresh_button_x_ratio` / `refresh_button_y_ratio`：刷新按钮的固定比例回退定位参数

### `openclaw`

- `enabled`：OpenClaw 总开关
- `mode`：`cli` 或 `http`

## OpenClaw 通知

当前所有 OpenClaw 通知都已经改为异步发送，不阻塞主线程：

- 启动通知
- DigUpTreasure 通知

当前实际相关配置项为：

- `startup.openclaw_message_enabled`
- `dig_up_treasure.openclaw_message_enabled`
- `openclaw.enabled`
- `openclaw.mode`

## 已知限制

- 本项目仍然是视觉自动化，不是直接读取游戏内部数据
- 极端窗口布局、遮挡、动画过场、跑马灯横幅仍可能影响识别稳定性
- 货车列表、车站图标、刷新按钮、分享弹窗等流程都已加入回退逻辑，但不能保证所有环境下零误差

## 打包发布

推荐使用：

```powershell
pyinstaller --clean --noconfirm LastWarBot.spec
```

然后将以下内容整理到发布目录：

- `releases/vX.Y.Z/package/LastWarBot/`
  - 放入 `dist/LastWarBot/` 解压后的 EXE 目录
  - 复制 `releases/start.bat.template` 为 `start.bat`
  - 补齐 `config.yaml`、`README.md`、`LICENSE`
  - 保留 `images/templates/`、`sounds/`、`logs/`
- `releases/vX.Y.Z/LastWarBot-vX.Y.Z-win-x64.zip`
  - 存放对应版本发布包
- `releases/vX.Y.Z/RELEASE_NOTES_vX.Y.Z.md`
  - 存放对应版本的 Release Notes

## 建议不提交的本地文件

- `.venv/`
- `build/`
- `dist/`
- `release/`
- `releases/*/package/`
- `logs/`
- `tmp_video_frames/`
- `*.zip`
- `images/samples/`
