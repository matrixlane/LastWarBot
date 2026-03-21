# LastWarBot

`LastWarBot` 是一款面向 Windows 11 的 Last War: Survival 自动化辅助工具.

## 主要功能

- 自动等待并检测 `LastWar.exe`
- 自动激活游戏窗口并将客户区调整为 `1920x1080`
- 自动识别并点击 同盟帮助
- 自动识别 挖掘机, 可通过 `OpenClaw` 通知 `QQBot`
- 识别 资源: 等级, 体力, 粮食, 铁矿, 金币, 战力, 钻石
- 支持进入 货车界面, 检查 `UR碎片`

## 运行环境

- Windows 11
- `Last War: Survival` PC 版
- Python `3.11`
- OCR `PaddleOCR + PaddlePaddle`

## 目录说明

- `lastwar_bot/` : 主程序源码
- `images/templates/` : 运行必需模板图
- `images/samples/` : 本地调试样本图
- `sounds/` : 提示音文件
- `logs/events/` : 事件日志
- `config.yaml` : 配置文件
- `release/LastWarBot/` : EXE 发布目录

## 快速开始

### Python 方式

```powershell
pip install -e .
python -m lastwar_bot --config config.yaml
```

也可直接运行 `start.bat` .

### EXE 方式

无需安装Python, 直接运行:

- `release/LastWarBot/LastWarBot.exe`
- `release/LastWarBot/start.bat`

注意: 发布时请保留整个 `release/LastWarBot/` 目录.

## 配置说明

当前配置按4个业务分栏组织:

1. 资源
- `ocr.enabled` : OCR 总开关
- `ocr.stats_enabled` : 资源识别开关
- `ocr.interval_seconds` : 资源刷新周期

2. 同盟帮助
- `cooldowns.handshake_seconds` : 同盟帮助冷却时间
- `sounds.handshake_enabled` : 同盟帮助提示音开关

3. 挖掘机
- `matching.thresholds.excavator` : 挖掘机匹配阈值
- `matching.regions.excavator` : 挖掘机检测区域
- `cooldowns.excavator_alert_seconds` : 挖掘机提醒冷却时间
- `openclaw.enabled` : OpenClaw 总开关
- `openclaw.cli_executable` : OpenClaw CLI 路径
- `openclaw.cli_target` : QQBot 目标地址

4. 货车
- `cargo.min_target_power_m` : 货车战力上限
- `cargo.ur_fragment_alert_count` : `UR碎片` 数量阈值
- `cargo.max_refresh_attempts` : 货车刷新上限

## 热键说明

- `F12` : 暂停 / 恢复 Bot
- `F5` : 从基地重新定位车站并进入货车搜索
- `F6` : 货车搜索中用于暂停 / 继续, 或跳过当前目标货车
- `Ctrl-C` : 退出 Bot

## 货车搜索流程

1. 在基地界面按 `F5`
2. 程序自动缩小地图并点击车站
3. 进入货车界面后识别金色和紫色货车
4. 逐一检查战力和 `UR碎片`
5. 未找到目标时自动刷新货车列表

## 事件日志

`logs/events/YYYY-MM-DD.log` 中默认记录 同盟帮助 和 挖掘机.

## GitHub 发布建议

建议上传:
- `lastwar_bot/`
- `images/templates/`
- `sounds/`
- `README.md`
- `LICENSE`
- `config.yaml`
- `pyproject.toml`
- `LastWarBot.spec`

建议不要上传:
- `.venv/`
- `build/`
- `dist/`
- `release/`
- `logs/`
- `images/samples/`
