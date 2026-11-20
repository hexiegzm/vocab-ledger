# 每周自动生成报告

调度刻意没有写进脚本：脚本只负责「渲染」，系统只负责「什么时候渲染」。这样换调度器不用改代码，脚本也不会在你只想跑一次的时候偷偷起个定时器。

目标只有一条：**每周日晚上跑一次 `report`，报告自动打开。**

---

## Linux / macOS（cron）

```bash
crontab -e
```

加一行（每周日 20:00）：

```cron
0 20 * * 0 cd /path/to/vocab-ledger && /usr/bin/python3 vocab.py report --open >> logs/cron.log 2>&1
```

- 路径写绝对路径，cron 的环境变量和你的终端不一样。
- `--open` 在 macOS 上走 `open`，Linux 上走 `xdg-open`；没有桌面环境时它会打印一行提示，报告照样生成，删掉 `--open` 即可。

## macOS（launchd，更可靠）

`~/Library/LaunchAgents/com.user.vocab-ledger.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.user.vocab-ledger</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/you/vocab-ledger/vocab.py</string>
    <string>report</string>
    <string>--open</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/you/vocab-ledger</string>
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/Users/you/vocab-ledger/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>/Users/you/vocab-ledger/logs/launchd.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.user.vocab-ledger.plist
```

## Windows（计划任务）

先写一个 `weekly.cmd`（放仓库根目录，**只写 ASCII 字符**——`cmd.exe` 在 `chcp` 生效前就解析了行）：

```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
python vocab.py report --open >> "logs\weekly.log" 2>&1
echo exit=%errorlevel% >> "logs\weekly.log"
```

注册任务（PowerShell，管理员不需要）：

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\path\to\vocab-ledger\weekly.cmd" -WorkingDirectory "C:\path\to\vocab-ledger"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "20:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "VocabLedgerWeekly" -Action $action -Trigger $trigger -Settings $settings -Force
```

常用操作：

```powershell
Start-ScheduledTask  -TaskName "VocabLedgerWeekly"                    # 立刻跑一次
Get-ScheduledTask    -TaskName "VocabLedgerWeekly" | Get-ScheduledTaskInfo   # 看上次结果
Unregister-ScheduledTask -TaskName "VocabLedgerWeekly" -Confirm:$false       # 取消
```

`StartWhenAvailable` 的作用：周日那天没开机，下次开机补跑一次。

## 为什么不内置调度器

三个理由：

1. **跨平台**。cron、计划任务、launchd 已经解决了时区、休眠、补跑，自己写一个只会更差。
2. **可测试**。渲染是纯函数，调度是外部触发，出问题时只需分别验证「数据对不对」和「有没有跑」。
3. **不越界**。脚本不该有常驻进程，也不该在 `import` 时起线程——那是库最让人讨厌的行为之一。

## 让它更靠谱一点

- 报告生成失败时，`report` 的退出码非 0；调度器可以接告警（`|| mail ...`）。
- 想留档就每周换个文件名：`report --out reports/$(date +%Y-W%V).html`。
- 想在手机上看到，把 `reports/` 放进任何同步盘，或起个静态服务——但那就违背「不联网」了，自行取舍。
