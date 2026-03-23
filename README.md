# LastWarBot

`LastWarBot` 是一款面向 Windows 的《Last War: Survival》自动化辅助工具。

当前版本重点支持：

- 自动等待并检测 `LastWar.exe`
- 自动激活游戏窗口
- 自动识别并点击同盟帮助
- 自动识别挖掘机，并可通过 `OpenClaw` 发送通知
- OCR 识别资源信息：等级、体力、粮食、铁矿、金币、战力、钻石
- 从基地界面进入车站，搜索货车，识别 `UR碎片`
- 根据 `UR碎片` 数量和货车战力条件筛选目标货车
- 在不同窗口尺寸、不同 DPI 缩放下做自适应识别

## 运行环境

- Windows 11
- 《Last War: Survival》PC 版
- Python `3.13`
- OCR：`PaddleOCR + PaddlePaddle`

当前项目已经按 Python `3.13` 路线适配。

## 目录说明

- `lastwar_bot/`：主程序源码
- `images/templates/`：模板图像
- `images/samples/`：本地调试样本
- `sounds/`：提示音
- `logs/events/`：事件日志
- `logs/LastWarBot_latest.log`：最近一次运行的控制台日志
- `config.yaml`：主配置文件
- `release/LastWarBot/`：发布目录

## 快速开始

### Python 方式

```powershell
pip install -e .
python -m lastwar_bot --config config.yaml
```

也可以直接运行：

```powershell
start.bat
```

### EXE 方式

如果你使用发布版，可以直接运行：

- `release/LastWarBot/LastWarBot.exe`
- `release/LastWarBot/start.bat`

发布时请保留整个 `release/LastWarBot/` 目录。

## 当前行为说明

### 窗口与分辨率

- 程序默认不再强制把游戏窗口改成 `1920x1080`
- 当前要求最小客户区不低于 `1024x728`
- 识别逻辑会根据当前客户区尺寸动态缩放
- 货车界面会先识别中间浮层，再在浮层内部识别货车与刷新按钮

这意味着程序不再只适配单一的 `4K + 150% DPI` 环境，而是尽量兼容不同显示器、不同 DPI 缩放、不同窗口尺寸。

### 日志

- 控制台日志每次运行都会同步保存到：
  - [logs/LastWarBot_latest.log](C:/Users/matri/source/repos/GitHub/matrixlane/LastWarBot/logs/LastWarBot_latest.log)
- 每次启动会覆盖上一次的 `LastWarBot_latest.log`
- 同盟帮助、挖掘机等事件日志仍保存在：
  - `logs/events/YYYY-MM-DD.log`

## 热键说明

- `F12`：暂停 / 恢复主循环
- `F5`：从基地界面重新定位车站并开始货车搜索
- `F6`：货车搜索中用于暂停 / 继续，或跳过当前目标货车
- `F2`：开启 / 停止鼠标当前位置极速连点
- `Ctrl-C`：退出程序

### F2 连点的限制

- 只有在 `F12` 已经让主循环暂停时，`F2` 才会生效
- 如果主循环恢复运行，连点会自动停止

这样可以避免 `F2` 连点和 `F12` 主循环同时操作鼠标，互相干扰。

## 货车搜索流程

1. 在基地界面按下 `F5`
2. 程序自动缩小地图并查找车站图标
3. 如果车站图标置信度过低，会自动平移地图重试
4. 如仍不足，会再回拉一点缩放后重试
5. 进入货车界面后，程序先识别货车浮层，再只在浮层内识别货车
6. 逐辆进入详情页，先识别 `UR碎片`
7. 只有 `UR碎片` 数量达到阈值时，才执行战力 OCR 核实
8. 若当前页没有目标货车，则点击刷新按钮并继续搜索

## 关键配置

### window

- `process_name`：进程名，默认 `LastWar.exe`
- `title_contains`：窗口标题关键字
- `min_client_width` / `min_client_height`：最小客户区要求
- `resize_enabled`：是否自动调整窗口大小
- `force_foreground_each_cycle`：每轮是否强制激活窗口

### matching

- `images_dir`：模板目录
- `thresholds.*`：各模板识别阈值
- `regions.*`：各识别区域

### ocr

- `enabled`：OCR 总开关
- `stats_enabled`：资源 OCR 开关
- `interval_seconds`：资源 OCR 周期
- `language`：OCR 语言
- `use_gpu`：是否启用 GPU

### cargo

- `min_target_power_m`：允许的货车战力上限，单位百万
- `ur_fragment_alert_count`：提醒所需的 `UR碎片` 数量
- `max_refresh_attempts`：最多刷新次数
- `inspection_wait_seconds`：点击货车详情后的等待时间
- `refresh_wait_seconds`：刷新后的等待时间
- `enter_wait_seconds`：进入货车界面后的首轮等待时间
- `enter_retry_count`：进入货车界面后的稳定性重试次数
- `sample_attempts`：每轮货车采样次数
- `sample_interval_seconds`：采样间隔
- `empty_result_retry_rounds`：空结果延迟重试次数

### debug

- `enabled`：调试总开关
- `log_environment_once`：启动时输出一次环境信息
- `log_cycle_state`：输出每轮状态
- `log_failed_detections`：输出识别失败探针日志
- `log_ocr_regions`：输出 OCR 实际裁剪区域

## 目前已知限制

- 本项目仍然是视觉自动化，不是直接读游戏内部数据
- 某些极端窗口布局、遮挡、动画过场、跑马灯横幅仍会影响识别稳定性
- 车站图标、货车详情热区、刷新按钮等逻辑已经增加了回退策略，但仍不保证所有环境下零误差

如果要分发给更多用户，建议：

- 保留默认 `config.yaml`
- 保留 `images/templates/`
- 保留 `sounds/`
- 保留 `logs/` 目录
- 先用 `F12` 暂停模式配合 `F2` 连点测试基础输入是否正常

## GitHub 发布建议

建议上传：

- `lastwar_bot/`
- `images/templates/`
- `sounds/`
- `README.md`
- `LICENSE`
- `config.yaml`
- `pyproject.toml`
- `LastWarBot.spec`
- `start.bat`

建议不要上传：

- `.venv/`
- `build/`
- `dist/`
- `release/`
- `logs/`
- `images/samples/`
