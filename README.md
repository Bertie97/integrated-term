# Integrated Term Cockpit

Git repository: `git@github.com:Bertie97/integrated-term.git`

## 跨平台命令

程序启动时会自动识别 `Windows`、`macOS` 或 `Ubuntu`，并选择对应的 shell、终端和进程树控制 API。配置中的根目录会将常见的 `/Users/...`、`/home/...` 和 `C:\\Users\\...` 用户目录映射到当前用户目录。

普通命令可以直接填写；当前支持的常见 POSIX 语法（如 `cd`、`env -u`、反斜杠换行和用户目录路径）会在 Windows 启动时自动转换。需要完全不同的启动方式时，可在同一个命令字段中按平台提供命令块：

```text
[macos]
uv run python tools/viewer.py
[ubuntu]
uv run python tools/viewer.py --headless
[windows]
py tools\\viewer.py --headless
```

Windows 命令块使用 `\\` 表示路径分隔符，多行命令使用 `^` 续行；macOS/Ubuntu 命令块使用 `/` 和 `\\` 续行。如果没有匹配当前系统的块，程序使用 `[default]`；如果没有任何平台块，则使用整段命令并执行通用转换。

一个轻量的进程编排 GUI：适合把脑机接口采集、解码、设备桥接和可视化程序放在同一工作台维护。

## 启动

安装 uv 后，在本项目目录运行：

```bash
uv run python app.py
```

项目通过 `.python-version` 固定使用 uv 管理的 Python 3.13.14，首次运行会按需下载解释器并在本项目内创建 `.venv`。无需激活环境或指定本机 Python 路径，也不会使用父目录的项目环境。控制台没有额外的 pip 运行依赖；Tk 随 Python 发行版提供。配置采用 uv 的 [`python-preference = "only-managed"`](https://docs.astral.sh/uv/reference/settings/#python-preference)。旧的 Python 3.13.5 构建在虚拟环境中存在 Tcl 资源定位问题，因此不要复用该旧版本。

上述配置只管理控制台自身的环境；进程列表里的命令仍使用各自项目的运行环境。

### Linux 打包

Linux 使用 PyInstaller `onedir` 打包，避免把 Tk、Font Awesome 资源和窗口控制工具隐藏在单文件临时目录中：

```bash
python3 -m pip install --user pyinstaller
bash packaging/linux/build_app.sh
```

输出位于 `dist/集成终端控制台/`，同时生成 `dist/集成终端控制台-linux.tar.gz`。将目录复制到 Linux 主机后，可把其中的 `.desktop` 文件安装到 `~/.local/share/applications/`；窗口显示/隐藏在 X11 下需要主机安装 `wmctrl` 或 `xdotool`。

程序只依赖 Python 标准库和 Tk。macOS/Linux 会优先用 `script` 为每个进程创建伪终端会话；macOS 上会通过 System Events 隐藏/显示子进程的 Qt/Tk 窗口。若系统未授权“辅助功能”，日志和 PID 管理仍可用，但窗口显示/隐藏需要在系统设置中给终端或 Python 授权。

## 使用

1. 用 `Set root` 选择所有进程的工作目录。
2. 使用顶部的 `保存配置` 将根目录和进程列表导出为 JSON，使用 `导入配置` 恢复配置；导入前会校验文件格式并停止当前进程。
3. 用 `＋ 新建分组` 创建一层进程分组，再用 `＋ 添加进程` 添加进程；新进程会放入当前选中的分组。
4. “无分组”长期存在且不可重命名/删除；其他组可双击标题重命名，标题三角按钮可展开/折叠。
5. 进程行可以拖拽排序或移动到其他组，组标题拖拽会携带整组进程交换顺序；双击进程可以直接启动。
6. 选中分组可统一启动/终止和控制组内 GUI；编辑按钮对分组自动置灰，移除分组时会确认是否连带删除内部进程。
7. 可用 Shift/Cmd（Windows/Linux 使用 Shift/Ctrl）多选进程；多选后按钮会变为“全部启动”和“全部终止”。
8. 选中进程会显示其终端输出，并尝试显示该进程创建的 GUI；切换到其它进程会隐藏前一个 GUI。运行中的进程行会以绿色显示。
9. 编辑进程时，Enter 保存、Esc 取消、Tab 切换输入控件；需要在命令框中换行时使用 Shift+Enter 或直接粘贴多行文本。
10. 移除进程前会弹出确认；运行中的进程不能直接移除，需要先终止。
11. 关闭控制台且仍有运行中进程时，会确认是否同步终止这些进程。
12. 终止单个进程或分组会先确认；运行中进程点击“配置”可查看名称和命令但不能修改。

进程配置保存在 `processes.json`，日志只保留当前会话最近 4000 行，不写入仓库日志文件。

## LSL 发布进程注册

添加或编辑进程时，程序从实际 Python 入口沿本地 import 静态查找 `StreamInfo`，并把候选流的 `name`、`type` 和相对证据路径保存到该进程的 `lsl_streams` 配置。旧配置会在下次启动时自动补齐。静态结果表示该启动入口可能创建的流；是否实际发布仍以 LSL Viewer 的运行时发现为准。

每次启动进程后，程序把管理进程的 PID、命令、仓库路径、启动时间、主机名和候选流写入系统级活动列表；正常退出后自动移除。系统级文件位置为：

- macOS：`~/Library/Application Support/BCIControl/active_lsl_processes.json`
- Windows：`%APPDATA%\BCIControl\active_lsl_processes.json`
- Linux：`$XDG_CONFIG_HOME/BCIControl/active_lsl_processes.json`，未设置时使用 `~/.config`

配置内的代码证据路径统一保存为相对仓库路径和 `/` 分隔符，避免把一台机器的绝对路径带到另一台机器。
