# LastWarBot

`LastWarBot` 是一个面向 Windows 的《Last War: Survival》视觉自动化辅助工具。

当前版本重点能力：

- 自动等待并检测 `LastWar.exe`
- 自动激活游戏窗口
- 自动识别并点击 Alliance Help
- 自动识别 DigUpTreasure 事件，并可选发送 OpenClaw 通知
- 玩家信息识别：等级、体力、粮食、铁矿、金币、战力、钻石
- 从基地进入车站，搜索货车，识别 `UR碎片`
- 根据 `UR碎片` 与战力条件筛选目标货车
- 可选自动分享目标货车到 `R4 & R5` 或同盟群
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
- 事件型日志仍保存到 `logs/events/YYYY-MM-DD.log`

### 热键

- `F12`：暂停 / 恢复实时监控
- `F5`：从基地重新定位车站并开始货车搜索
- `F6`：货车搜索中用于暂停 / 继续，或跳过当前目标货车
- `F2`：在鼠标当前位置开启 / 停止极速连点
- `Ctrl-C`：退出程序

### F2 连点限制

- 只有在 `F12` 已经让主循环暂停时，`F2` 才会生效
- 如果主循环恢复运行，连点会自动停止

## 货车搜索流程

1. 在基地界面按下 `F5`
2. 程序缩小地图并查找车站图标
3. 若车站图标置信度过低，会自动平移地图重试
4. 进入货车界面后，先识别中间货车浮层
5. 在浮层内识别货车列表
6. 逐辆进入详情页，先识别 `UR碎片`
7. 只有当 `UR碎片` 数量达到搜索阈值时，才进行战力核实
8. 若满足搜索条件：
   - 会先根据 `truck.alert_enabled` 决定是否提醒
   - 若没有命中分享，则自动停下等待人工处理
   - 若命中分享，则会先按优先级判断分享目标
   - 先判断 `r4r5_share`
   - 未命中时再判断 `alliance_share`
   - 同盟群不依赖文字识别，而是点击分享弹窗第二行
   - 命中后会点击确认分享弹窗中的 `分享`
   - 分享成功后继续搜索下一辆
9. 若当前页没有目标货车，则自动刷新继续搜索

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

### `truck`

- `min_target_power_m`：允许的货车战力上限，单位百万
- `min_ur_shards`：货车搜索最小 `UR碎片` 阈值；满足后即使不提醒、不分享也会停下
- `alert_enabled`：满足货车搜索条件时是否提醒
- `r4r5_share.enabled`：是否开启 `R4 & R5` 自动分享
- `r4r5_share.min_ur_shards`：`R4 & R5` 自动分享所需的最少 `UR碎片`
- `alliance_share.enabled`：是否开启同盟群自动分享
- `alliance_share.min_ur_shards`：同盟群自动分享所需的最少 `UR碎片`
- 当两个分享规则都开启时，始终优先匹配 `R4 & R5`
- `share_wait_seconds`：点击分享后的等待时间
- `share_confirm_wait_seconds`：点击目标群与确认分享后的等待时间
- `max_refresh_attempts`：最多刷新次数
- `inspection_wait_seconds`：点击货车详情后的等待时间
- `refresh_wait_seconds`：刷新后的等待时间
- `enter_wait_seconds`：首次进入货车界面后的等待时间
- `enter_retry_count`：货车列表重试次数
- `sample_attempts`：货车列表单轮采样次数
- `sample_interval_seconds`：货车列表采样间隔
- `ur_shard_confirm_interval_seconds`：`UR碎片` 二次复核等待时间
- `empty_result_retry_rounds`：空结果延迟重试轮数

### `openclaw`

- `enabled`：OpenClaw 总开关
- `mode`：`cli` 或 `http`

## OpenClaw 通知

当前所有 OpenClaw 通知都已经改为异步发送，不阻塞主线程：

- 启动通知
- DigUpTreasure 通知

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
