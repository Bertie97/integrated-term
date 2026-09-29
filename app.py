#!/usr/bin/env python3
"""Integrated Term process cockpit.

The application is intentionally dependency-light: it uses Tk for the shell,
``script``/pipes for pseudo-terminal style sessions, and optional native window
controls for child Qt/Tk applications.
"""
from __future__ import annotations

import json
import os
import platform
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
import ctypes
import ctypes.wintypes
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

from lsl_registry import (
    build_entry,
    prune_registry,
    register_process,
    scan_python_cli_option,
    scan_lsl_streams,
    unregister_process,
)
from branding import horizontal_brand
from version import VERSION_STRING


APP_DIR = Path(__file__).resolve().parent
BUNDLED_CONFIG_PATH = APP_DIR / "processes.json"


def child_environment() -> dict[str, str]:
    """Prevent the frozen Tk runtime from contaminating launched applications."""
    environment = os.environ.copy()
    environment.pop("TCL_LIBRARY", None)
    environment.pop("TK_LIBRARY", None)
    return environment


def user_config_path(target: str | None = None) -> Path:
    if target is None:
        target = {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux")
    if target == "macos":
        return Path.home() / "Library" / "Application Support" / "IntegratedTerm" / "processes.json"
    if target == "windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "IntegratedTerm" / "processes.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "integrated-term" / "processes.json"


CONFIG_PATH = user_config_path() if getattr(sys, "frozen", False) else BUNDLED_CONFIG_PATH
FA_ICON_DIR = APP_DIR / "assets" / "icons" / "fa"
ANSI_CSI = re.compile(r"\x1b\[([0-9;]*)m")
PLATFORM_BLOCK = re.compile(r"(?ms)^\[(windows|macos|ubuntu|linux|default)\]\s*\n(.*?)(?=^\[(?:windows|macos|ubuntu|linux|default)\]\s*$|\Z)")


UI_TEXT = {
    "zh": {
        "app_title": "集成终端控制台", "integrated": "集成终端", "console": "控制台",
        "set_root": "设置根目录", "import_config": "导入配置", "export_config": "保存配置",
        "process_list": "进程列表", "add_process": "添加进程", "add_workflow": "添加工作流", "new_group": "新建分组",
        "name": "名称", "gui": "界面", "state": "状态", "output": "终端输出",
        "start": "启动", "stop": "终止", "start_group": "启动分组", "stop_group": "终止分组",
        "start_all": "全部启动", "stop_all": "全部终止", "restart": "重启", "restart_all": "全部重启",
        "auto": "自动调度", "show": "保持显示", "hide": "保持隐藏", "edit": "编辑", "configure": "配置",
        "remove": "移除", "terminal": "终端", "ready": "就绪", "no_process": "未选择进程",
        "running": "运行中", "stopping": "正在终止", "stopped": "已停止", "idle": "空闲", "exited": "已退出", "lines": "行", "language": "EN", "process_config": "进程配置",
        "add_process_title": "添加进程", "edit_process_title": "编辑进程", "process_name": "进程名称",
        "supports_lang": "适配中英文切换（自动添加 --lang ch/en）",
        "cli_command": "CLI 命令", "newline_hint": "使用⇪+↩︎换行", "close": "关闭", "cancel": "取消",
        "save": "保存", "incomplete": "信息不完整", "fill_required": "请填写进程名称和 CLI 命令。",
        "remove_group": "移除分组", "remove_group_question": "将移除选中的 {count} 个分组。是否同时移除组内进程？",
        "release": "释放", "no": "否", "yes": "是", "cannot_remove_group": "无法移除分组",
        "stop_group_first": "请先终止分组内所有进程。", "remove_process": "移除进程",
        "confirm_remove": "确定移除选中的 {count} 个进程吗？", "stop_process": "终止进程",
        "confirm_stop_all": "确定终止{label}内的全部进程吗？", "confirm_stop_one": "确定终止进程“{name}”吗？",
        "exiting": "退出控制台", "confirm_exit": "关闭控制台将同步终止所有控制台唤起的进程，是否继续？",
        "import_failed": "导入失败", "save_failed": "保存失败", "invalid_config": "配置文件格式无效：\n{error}",
        "cannot_save": "无法保存配置文件：\n{error}", "imported": "已导入 {items} 个进程、{groups} 个分组",
        "saved": "配置已保存：{target}", "root_changed": "根目录：{root}", "exited": "{name} 已退出，返回码：{code}", "exited_state": "已退出",
        "json_config": "JSON 配置", "all_files": "所有文件", "terminal_output": "终端输出", "workflow_title": "工作流配置", "workflow_available": "可用项目（双击添加）", "workflow_item": "当前工作流", "terminal_commands": "实际终端命令", "add_item": "添加项目", "plain_text_edit": "以纯文本编辑", "workflow_edit": "以工作流编辑", "remove_item": "删除子进程", "sleep_item": "等待 {seconds} 秒", "sleep_seconds": "等待秒数", "invalid_workflow": "工作流至少需要一个项目。",
    },
    "en": {
        "app_title": "Integrated Terminal Cockpit", "integrated": "Integrated Terminal", "console": "Cockpit",
        "set_root": "Set Root", "import_config": "Import Config", "export_config": "Save Config",
        "process_list": "Processes", "add_process": "Add Process", "add_workflow": "Add Workflow", "new_group": "New Group",
        "name": "Name", "gui": "GUI", "state": "State", "output": "Terminal Output",
        "start": "Start", "stop": "Stop", "start_group": "Start Group", "stop_group": "Stop Group",
        "start_all": "Start All", "stop_all": "Stop All", "restart": "Restart", "restart_all": "Restart All",
        "auto": "Auto", "show": "Keep Visible", "hide": "Keep Hidden", "edit": "Edit", "configure": "Configure",
        "remove": "Remove", "terminal": "Terminal", "ready": "Ready", "no_process": "No process selected",
        "running": "Running", "stopping": "Stopping", "stopped": "Stopped", "idle": "Idle", "exited": "Exited", "lines": "lines", "language": "中文", "process_config": "Process Configuration",
        "add_process_title": "Add Process", "edit_process_title": "Edit Process", "process_name": "Process Name",
        "supports_lang": "Supports Chinese/English switching (adds --lang ch/en)",
        "cli_command": "CLI Command", "newline_hint": "Use ⇪+↩︎ for a new line", "close": "Close", "cancel": "Cancel",
        "save": "Save", "incomplete": "Incomplete", "fill_required": "Enter a process name and CLI command.",
        "remove_group": "Remove Group", "remove_group_question": "Remove {count} selected group(s). Also remove their processes?",
        "release": "Release", "no": "No", "yes": "Yes", "cannot_remove_group": "Cannot Remove Group",
        "stop_group_first": "Stop all processes in the group first.", "remove_process": "Remove Process",
        "confirm_remove": "Remove {count} selected process(es)?", "stop_process": "Stop Process",
        "confirm_stop_all": "Stop all processes in {label}?", "confirm_stop_one": "Stop process “{name}”?",
        "exiting": "Exit Cockpit", "confirm_exit": "Exiting will stop all processes launched by this cockpit. Continue?",
        "import_failed": "Import Failed", "save_failed": "Save Failed", "invalid_config": "Invalid config file:\n{error}",
        "cannot_save": "Could not save config file:\n{error}", "imported": "Imported {items} process(es), {groups} group(s)",
        "saved": "Config saved: {target}", "root_changed": "Root: {root}", "exited": "{name} exited, return code: {code}", "exited_state": "Exited",
        "json_config": "JSON config", "all_files": "All files", "terminal_output": "Terminal Output", "workflow_title": "Workflow Configuration", "workflow_available": "Available (double-click to add)", "workflow_item": "Current Workflow", "terminal_commands": "Resolved Terminal Commands", "add_item": "Add Item", "plain_text_edit": "Edit as Plain Text", "workflow_edit": "Edit as Workflow", "remove_item": "Remove Child", "sleep_item": "Wait {seconds} seconds", "sleep_seconds": "Wait seconds", "invalid_workflow": "A workflow needs at least one item.",
    },
}


def _text(lang: str, key: str, **kwargs) -> str:
    return UI_TEXT[lang][key].format(**kwargs)


def detect_platform() -> str:
    """Return the command vocabulary used by the current host."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        try:
            os_release = Path("/etc/os-release").read_text(encoding="utf-8")
        except OSError:
            os_release = ""
        if re.search(r"^ID(?:_LIKE)?=.*\bubuntu\b", os_release, re.MULTILINE):
            return "ubuntu"
        return "linux"
    return "posix"


def resolve_config_path(value: str, target: str | None = None) -> Path:
    """Resolve paths saved on another supported operating system."""
    target = target or detect_platform()
    value = os.path.expandvars(str(value)).strip()
    if value.startswith("~"):
        return Path(value).expanduser()
    if target == "windows":
        match = re.match(r"^[A-Za-z]:[\\/]Users[\\/]([^\\/]+)(.*)$", value)
        if match:
            return Path.home() / match.group(2).lstrip("\\/").replace("\\", os.sep)
    match = re.match(r"^/(?:Users|home)/[^/]+(?P<suffix>/.*)?$", value)
    if match:
        return Path.home() / (match.group("suffix") or "").lstrip("/")
    return Path(value).expanduser()


def select_platform_command(command: str, target: str | None = None) -> tuple[str, str]:
    """Select an optional platform block and return its source context."""
    target = target or detect_platform()
    blocks = {match.group(1): match.group(2).strip() for match in PLATFORM_BLOCK.finditer(command)}
    if not blocks:
        return command.strip(), "auto"
    return blocks.get(target, blocks.get("default", "")).strip(), target


def translate_command(command: str, target: str | None = None) -> str:
    """Translate the supported cross-platform shell subset at launch time."""
    target = target or detect_platform()
    selected, _context = select_platform_command(command, target)
    selected = selected.replace("\\\n", " ")
    if target != "windows":
        return selected.strip()
    selected = re.sub(r"(?<![A-Za-z]):?~/", "%USERPROFILE%/", selected)
    selected = re.sub(r"/(?:Users|home)/[^/\s]+", "%USERPROFILE%", selected)
    selected = re.sub(r"(?m)^\s*cd\s+(.+?)\s*$", lambda match: f'cd /d "{match.group(1).strip().strip(chr(34))}"', selected)
    selected = re.sub(r"\benv\s+-u\s+([A-Za-z_][A-Za-z0-9_]*)\s+", r'set "\1=" && ', selected)
    selected = re.sub(r"(?m)^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*?)\s*$", r'set "\1=\2"', selected)
    return selected.strip()


def command_with_language(command: str, lang: str, target: str | None = None) -> str:
    translated = translate_command(command, target)
    value = "ch" if lang == "zh" else "en"
    if re.search(r"(?<!\S)--lang(?:=|\s+)\S+", translated):
        return re.sub(r"(?<!\S)--lang(?:=|\s+)\S+", f"--lang {value}", translated)
    return f"{translated.rstrip()} --lang {value}".strip()


MACRO_LINE = re.compile(r"^\s*\[([^\]]+)\]\((.*)\)\s*$")
SLEEP_LINE = re.compile(r"^\s*(?:sleep|timeout|Start-Sleep)\s+\S+(?:\s+.*)?$", re.IGNORECASE)
SLEEP_MACRO_TITLE = "sleep"
DURATION_TOKEN = re.compile(r"(?P<value>\d+(?:\.\d+)?)(?P<unit>ms|s|m|h)", re.IGNORECASE)


def parse_macro_line(line: str) -> tuple[str, str] | None:
    """Parse one process reference line: ``[Title](--argv value **argu)``."""
    match = MACRO_LINE.match(line)
    if not match:
        return None
    title, arguments = match.groups()
    if not title.strip():
        return None
    return title.strip(), arguments.strip()


def macro_references(command: str) -> list[str]:
    return [parsed[0] for line in command.splitlines() if (parsed := parse_macro_line(line)) and parsed[0].lower() != SLEEP_MACRO_TITLE]


def parse_duration(value: str) -> float:
    """Parse a duration such as ``1h0.3s`` into seconds."""
    text = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    matches = list(DURATION_TOKEN.finditer(text))
    if not matches or "".join(match.group(0) for match in matches).lower() != text.lower():
        raise ValueError(f"等待时间格式无效：{value}")
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(match.group("value")) * scale[match.group("unit").lower()] for match in matches)


def sleep_command(value: str, target: str | None = None) -> str:
    seconds = parse_duration(value)
    if (target or detect_platform()) == "windows":
        milliseconds = max(0, round(seconds * 1000))
        return f'powershell -NoProfile -Command "Start-Sleep -Milliseconds {milliseconds}"'
    return f"sleep {seconds:g}"


def _apply_macro_arguments(command: str, arguments: str) -> str:
    """Apply short/long option overrides and removals to a referenced command."""
    try:
        tokens = shlex.split(arguments)
    except ValueError as exc:
        raise ValueError(f"宏参数格式无效：{arguments}") from exc
    replacements: dict[str, str] = {}
    flags: set[str] = set()
    removals: set[str] = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if re.fullmatch(r"\*\*[A-Za-z0-9][A-Za-z0-9_-]*", token):
            removals.add(f"--{token[2:]}")
            index += 1
            continue
        if re.fullmatch(r"\*[A-Za-z0-9]", token):
            removals.add(f"-{token[1:]}")
            index += 1
            continue
        if re.fullmatch(r"--[A-Za-z0-9][A-Za-z0-9_-]*", token):
            next_token = tokens[index + 1] if index + 1 < len(tokens) else None
            option_like = next_token is not None and (
                re.fullmatch(r"--[A-Za-z0-9][A-Za-z0-9_-]*", next_token)
                or re.fullmatch(r"-[A-Za-z]", next_token)
                or re.fullmatch(r"\*\*?[A-Za-z0-9][A-Za-z0-9_-]*", next_token)
            )
            if next_token is None or option_like:
                flags.add(token)
                index += 1
            else:
                replacements[token] = next_token
                index += 2
            continue
        if re.fullmatch(r"-[A-Za-z0-9]", token):
            if index + 1 >= len(tokens):
                raise ValueError(f"宏参数缺少参数值：{token}")
            replacements[token] = tokens[index + 1]
            index += 2
            continue
        raise ValueError(f"宏参数必须是 -x value、--name value、*x 或 **name：{token}")

    result = command
    shell_value = r"(?:'[^']*'|\"[^\"]*\"|(?!(?:--[A-Za-z0-9]|-[A-Za-z]))[^\s\\]+)"
    def option_pattern(option: str) -> str:
        return rf"(?<!\S){re.escape(option)}(?=$|[\t =])(?:={shell_value}|[\t ]+{shell_value})?"

    for option in removals:
        result = re.sub(option_pattern(option), "", result)
    for option in flags:
        pattern = option_pattern(option)
        result, count = re.subn(pattern, option, result)
        if count == 0:
            result = f"{result.rstrip()} {option}"
    for option, value in replacements.items():
        pattern = option_pattern(option)
        result, count = re.subn(pattern, f"{option} {shlex.quote(value)}", result)
        if count == 0:
            result = f"{result.rstrip()} {option} {shlex.quote(value)}"
    return result.strip()


def is_workflow_command(command: str) -> bool:
    """A workflow contains only references, sleep commands, and blank lines."""
    lines = [line.strip() for line in command.splitlines() if line.strip()]
    def valid_line(line: str) -> bool:
        parsed = parse_macro_line(line)
        if parsed:
            if parsed[0].lower() != SLEEP_MACRO_TITLE:
                return True
            try:
                return parse_duration(parsed[1]) >= 0
            except ValueError:
                return False
        return bool(SLEEP_LINE.match(line))
    return bool(lines) and all(valid_line(line) for line in lines)


def _spec_lookup(specs) -> dict[str, object]:
    lookup: dict[str, object] = {}
    for spec in specs:
        names = {spec.name, spec.name_zh, spec.name_en}
        for name in names:
            if name:
                lookup.setdefault(name.strip(), spec)
    return lookup


def validate_macro_cycles(specs) -> None:
    """Validate references and reject cycles before a configuration is saved."""
    lookup = _spec_lookup(specs)
    graph: dict[int, list[object]] = {}
    for spec in specs:
        refs = macro_references(spec.command)
        missing = [name for name in refs if name not in lookup]
        if missing:
            raise ValueError(f"宏引用不存在：{', '.join(missing)}")
        graph[id(spec)] = [lookup[name] for name in refs]
    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(spec):
        identity = id(spec)
        if identity in visiting:
            raise ValueError(f"检测到循环引用：{spec.name}")
        if identity in visited:
            return
        visiting.add(identity)
        for child in graph.get(identity, []):
            visit(child)
        visiting.remove(identity)
        visited.add(identity)

    for spec in specs:
        visit(spec)


def expand_macro_command(command: str, specs, cwd: Path, target: str | None = None, _stack: tuple[object, ...] = ()) -> str:
    """Expand macro lines recursively and reset each expansion to the caller cwd."""
    lookup = _spec_lookup(specs)
    output: list[str] = []
    for line in command.splitlines():
        parsed = parse_macro_line(line)
        if not parsed:
            output.append(line)
            continue
        title, arguments = parsed
        if title.lower() == SLEEP_MACRO_TITLE:
            output.append(sleep_command(arguments, target))
            continue
        referenced = lookup.get(title)
        if referenced is None:
            raise ValueError(f"宏引用不存在：{title}")
        if referenced in _stack:
            raise ValueError(f"检测到循环引用：{title}")
        expanded = expand_macro_command(referenced.command, specs, cwd, target, (*_stack, referenced))
        expanded = _apply_macro_arguments(expanded, arguments)
        output.append(expanded)
    return "\n".join(output)


def workflow_plain_text(command: str, specs, cwd: Path, target: str | None = None) -> str:
    """Expand workflow items into editable command blocks separated by blank lines."""
    blocks = [
        translate_command(expand_macro_command(line, specs, cwd, target), target)
        for line in command.splitlines()
        if line.strip()
    ]
    return "\n\n".join(block for block in blocks if block)


class PlatformRuntime:
    """System API and shell adapter used by the process manager."""

    def __init__(self, target: str | None = None):
        self.target = target or detect_platform()

    @property
    def is_windows(self) -> bool:
        return self.target == "windows"

    def command_args(self, command: str) -> list[str]:
        command = translate_command(command, self.target)
        return self.translated_command_args(command)

    def translated_command_args(self, command: str) -> list[str]:
        if self.is_windows:
            return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", command]
        shell = "/bin/sh"
        if self.target in {"macos", "ubuntu", "linux"} and shutil.which("script"):
            if self.target == "macos":
                # A universal2 Python can inherit Rosetta's x86_64 slice from
                # the GUI launcher. Keep the complete PTY subtree arm64 so
                # architecture-specific wheels in project venvs are usable.
                return ["/usr/bin/arch", "-arm64", "script", "-q", "/dev/null", shell, "-lc", command]
            return ["script", "-q", "-c", f"{shell} -lc {shlex.quote(command)}", "/dev/null"]
        if self.target == "macos":
            return ["/usr/bin/arch", "-arm64", shell, "-lc", command]
        return [shell, "-lc", command]

    def popen_kwargs(self) -> dict:
        if self.is_windows:
            return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        return {"start_new_session": True}


def probe_language_support(command: str, root_dir: Path, target: str | None = None, timeout: float = 3.0) -> bool:
    """Statically detect --lang support without launching the configured process."""
    del timeout  # Retained for compatibility with callers of the former runtime probe.
    selected, _context = select_platform_command(command, target)
    return scan_python_cli_option(selected, root_dir, "--lang")


def clean_terminal_text(value: str) -> str:
    """Apply the small terminal subset commonly emitted by package managers."""
    value = value.replace("\x04", "")
    value = ANSI_CSI.sub("", value)
    value = re.sub(r"\x1b\][^\a]*(?:\a|\x1b\\)", "", value)
    value = value.replace("\r", "")
    output: list[str] = []
    for char in value:
        if char == "\b":
            if output:
                output.pop()
        elif char == "\x1b":
            continue
        elif ord(char) >= 32 or char in {"\n", "\t"}:
            output.append(char)
    return "".join(output)


def ansi_segments(value: str):
    """Yield ``(text, tag)`` pairs for common ANSI SGR sequences."""
    state = "normal"
    cursor = 0
    for match in ANSI_CSI.finditer(value):
        chunk = clean_terminal_text(value[cursor:match.start()])
        if chunk:
            yield chunk, state
        codes = [int(code or 0) for code in match.group(1).split(";")]
        for code in codes:
            if code == 0:
                state = "normal"
            elif code == 1:
                state = "bold"
            elif code == 2:
                state = "dim"
            elif 30 <= code <= 37:
                state = f"color{code - 30}"
            elif code == 39:
                state = "normal"
        cursor = match.end()
    tail = clean_terminal_text(value[cursor:])
    if tail:
        yield tail, state


class RoundedButton(tk.Canvas):
    """Small borderless rounded button used where ttk cannot draw radii."""

    def __init__(self, parent, text, command, width=92, height=34, accent=False, icon=None, long_press_command=None, long_press_text="重启"):
        self._text = text
        self._command = command
        self._icon = icon
        self._accent = accent
        self._long_press_command = long_press_command
        self._long_press_text = long_press_text
        self._press_after = None
        self._press_started = None
        self._long_press_fired = False
        self._long_press_intent_ms = 180
        self._long_press_ms = 800
        self._enabled = True
        self._width, self._height = width, height
        self._icon_images: dict[tuple[str, str], tk.PhotoImage] = {}
        try:
            canvas_bg = parent.cget("background") or "#ffffff"
        except (AttributeError, tk.TclError):
            canvas_bg = "#ffffff"
        self._canvas_bg = canvas_bg
        super().__init__(parent, width=width, height=height, bg=canvas_bg, highlightthickness=0, bd=0)
        self.bind("<Button-1>", self._click)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda _event: self._draw(hover=True))
        self.bind("<Leave>", lambda _event: self._draw(hover=False))
        self._draw()

    def _draw(self, hover=False):
        self.delete("all")
        radius = 9
        enabled = self._enabled
        if self._accent:
            fill = "#075bbd" if hover and enabled else ("#0b72e7" if enabled else "#b9d1e8")
            foreground = "#ffffff"
        else:
            fill = "#dcecff" if hover and enabled else ("#eaf1f8" if enabled else "#f0f3f6")
            foreground = "#243954" if enabled else "#a8b4c1"
        self.create_arc(0, 0, radius * 2, radius * 2, start=90, extent=90, fill=fill, outline=fill)
        self.create_arc(self._width - radius * 2, 0, self._width, radius * 2, start=0, extent=90, fill=fill, outline=fill)
        self.create_arc(0, self._height - radius * 2, radius * 2, self._height, start=180, extent=90, fill=fill, outline=fill)
        self.create_arc(self._width - radius * 2, self._height - radius * 2, self._width, self._height, start=270, extent=90, fill=fill, outline=fill)
        self.create_rectangle(radius, 0, self._width - radius, self._height, fill=fill, outline=fill)
        self.create_rectangle(0, radius, self._width, self._height - radius, fill=fill, outline=fill)
        press_elapsed = (time.monotonic() - self._press_started) * 1000 if self._press_started is not None else 0
        pressing = bool(
            self._press_started is not None
            and self._long_press_command
            and enabled
            and press_elapsed >= self._long_press_intent_ms
        )
        if pressing:
            progress = min(1.0, max(0.0, press_elapsed - self._long_press_intent_ms) / (self._long_press_ms - self._long_press_intent_ms))
            size = 16
            left = self._width - size - 5
            top = (self._height - size) / 2
            self.create_arc(left, top, left + size, top + size, start=90, extent=-360 * progress,
                            style="arc", outline="#ffffff", width=2, tags="long-press-progress")
        display_text = self._long_press_text if pressing else self._text
        icon_width = 18 if self._icon else 0
        text_width = len(display_text) * 5.5
        content_width = text_width + (icon_width + 5 if self._icon else 0)
        content_left = (self._width - content_width) / 2
        if self._icon:
            if self._icon == "pin" and not self._text:
                icon_offset = 2
            elif display_text:
                # Chinese glyph metrics need the historic optical correction;
                # Latin/English labels use the mathematically centered icon.
                has_cjk = any("\u3400" <= char <= "\u9fff" for char in display_text)
                if not has_cjk:
                    icon_offset = 0
                elif display_text == "设置根目录":
                    # icon_offset = -6 if display_text == "设置根目录" else -3
                    icon_offset = -6
                elif len(display_text) == 4:
                    icon_offset = -8
                else:
                    # else -3 for Chinese labels
                    icon_offset = -3
            else:
                icon_offset = 0
            self._draw_icon(self._icon, content_left + icon_width / 2 + icon_offset, self._height / 2, foreground)
        text_x = content_left + (icon_width + 5 if self._icon else 0) + text_width / 2
        self.create_text(text_x, self._height / 2, text=display_text, fill=foreground, font=("SF Pro Display", 10, "bold" if self._accent else "normal"))

    def _draw_icon(self, icon, cx, cy, color):
        """Load a pre-rendered Font Awesome glyph through Tk itself."""
        variant = "white" if color == "#ffffff" else ("muted" if color == "#a8b4c1" else "dark")
        path = FA_ICON_DIR / f"{icon}-{variant}.png"
        try:
            cache_key = (icon, variant)
            if cache_key not in self._icon_images:
                self._icon_images[cache_key] = tk.PhotoImage(file=str(path))
            image = self._icon_images[cache_key]
        except tk.TclError as exc:
            raise RuntimeError(f"无法加载 Font Awesome 图标资源: {path}") from exc
        self.create_image(cx, cy, image=image)

    def _click(self, _event):
        if not self._enabled:
            return
        self._long_press_fired = False
        if self._long_press_command:
            self._press_started = time.monotonic()
            self._schedule_long_press()
        else:
            self._command()

    def _schedule_long_press(self):
        if self._press_started is None or not self._enabled:
            return
        elapsed = (time.monotonic() - self._press_started) * 1000
        if elapsed >= self._long_press_ms:
            self._long_press_fired = True
            self._press_after = None
            self._draw()
            return
        self._draw()
        self._press_after = self.after(30, self._schedule_long_press)

    def _release(self, _event):
        if self._press_after is not None:
            self.after_cancel(self._press_after)
            self._press_after = None
        was_pressed = self._press_started is not None
        elapsed = (time.monotonic() - self._press_started) * 1000 if was_pressed else 0
        self._press_started = None
        self._draw()
        if was_pressed and self._enabled:
            if self._long_press_fired:
                self._long_press_command()
            elif elapsed < self._long_press_intent_ms:
                self._command()
        self._long_press_fired = False

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if "icon" in kwargs:
            self._icon = kwargs.pop("icon")
        if "long_press_text" in kwargs:
            self._long_press_text = kwargs.pop("long_press_text")
        if "state" in kwargs:
            self._enabled = kwargs.pop("state") != "disabled"
        super().configure(**kwargs)
        self._draw()


class RoundedPanel(tk.Frame):
    """Stable panel container; native Tk manages its embedded controls."""

    def __init__(self, parent, padding=12):
        super().__init__(parent, bg="#f4f7fb", highlightthickness=0, bd=0)
        self._padding = padding
        self._canvas = tk.Canvas(self, bg="#f4f7fb", highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)
        self.content = tk.Frame(self, bg="#ffffff")
        self.content.place(x=padding, y=padding, relwidth=1, relheight=1, width=-padding * 2, height=-padding * 2)
        self._canvas.bind("<Configure>", self._draw)

    def _draw(self, event=None):
        width = event.width if event else self._canvas.winfo_width()
        height = event.height if event else self._canvas.winfo_height()
        radius = 12
        self._canvas.delete("all")
        self._canvas.create_arc(0, 0, radius * 2, radius * 2, start=90, extent=90, fill="#ffffff", outline="#d7e2ef")
        self._canvas.create_arc(width - radius * 2, 0, width, radius * 2, start=0, extent=90, fill="#ffffff", outline="#d7e2ef")
        self._canvas.create_arc(0, height - radius * 2, radius * 2, height, start=180, extent=90, fill="#ffffff", outline="#d7e2ef")
        self._canvas.create_arc(width - radius * 2, height - radius * 2, width, height, start=270, extent=90, fill="#ffffff", outline="#d7e2ef")
        self._canvas.create_rectangle(radius, 0, width - radius, height, fill="#ffffff", outline="#ffffff")
        self._canvas.create_rectangle(0, radius, width, height - radius, fill="#ffffff", outline="#ffffff")
        self._canvas.create_line(radius, 0, width - radius, 0, fill="#d7e2ef")
        self._canvas.create_line(radius, height, width - radius, height, fill="#d7e2ef")
        self._canvas.create_line(0, radius, 0, height - radius, fill="#d7e2ef")
        self._canvas.create_line(width, radius, width, height - radius, fill="#d7e2ef")


@dataclass
class ProcessSpec:
    name: str
    command: str
    enabled: bool = True
    lsl_streams: list[dict] = field(default_factory=list)
    name_zh: str | None = None
    name_en: str | None = None
    supports_lang: bool | None = None
    workflow: bool | None = None

    def localized_name(self, lang: str) -> str:
        """Return the requested process name, falling back across languages."""
        preferred = self.name_zh if lang == "zh" else self.name_en
        alternate = self.name_en if lang == "zh" else self.name_zh
        return (preferred or alternate or self.name).strip()


@dataclass
class ManagedProcess:
    spec: ProcessSpec
    process: subprocess.Popen | None = None
    lines: list[str] = field(default_factory=list)
    started_at: float | None = None
    return_code: int | None = None
    reader: threading.Thread | None = None
    waiter: threading.Thread | None = None
    stopping: bool = False
    gui_mode: str = "auto"  # auto: selected process is visible; show/hide are persistent
    run_id: int = 0

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None


@dataclass
class ProcessGroup:
    name: str
    items: list[ManagedProcess] = field(default_factory=list)
    locked: bool = False
    expanded: bool = True
    name_zh: str | None = None
    name_en: str | None = None

    def localized_name(self, lang: str) -> str:
        preferred = self.name_zh if lang == "zh" else self.name_en
        alternate = self.name_en if lang == "zh" else self.name_zh
        return (preferred or alternate or self.name).strip()


class WindowController:
    def __init__(self, root: tk.Misc):
        self.root = root
        self._last_visibility: dict[tuple[int, int], bool] = {}
        self._visibility_lock = threading.Lock()
        self._osascript_lock = threading.Lock()
        self._visibility_queue: queue.Queue[tuple[ManagedProcess, bool, bool] | None] = queue.Queue()
        self._visibility_worker = threading.Thread(target=self._visibility_loop, daemon=True)
        self._visibility_worker.start()

    def _visibility_loop(self):
        while True:
            request = self._visibility_queue.get()
            if request is None:
                return
            managed, visible, focus = request
            # Collapse stale toggles queued during a renderer transition.
            while True:
                try:
                    newer = self._visibility_queue.get_nowait()
                except queue.Empty:
                    break
                if newer is None:
                    return
                if newer[0] is managed:
                    visible = newer[1]
                    focus = focus or newer[2]
                else:
                    self._visibility_queue.put(newer)
                    break
            if managed.process is None or not managed.running:
                continue
            self._platform_window(managed.process.pid, visible, focus)
    """Best-effort visibility control for GUI descendants of a CLI process."""

    def _descendants(self, pid: int) -> list[int]:
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
            pairs = [tuple(map(int, line.split())) for line in output.splitlines() if len(line.split()) == 2]
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
        children: dict[int, list[int]] = {}
        for child, parent in pairs:
            children.setdefault(parent, []).append(child)
        found: list[int] = []
        pending = [pid]
        while pending:
            current = pending.pop()
            for child in children.get(current, []):
                found.append(child)
                pending.append(child)
        return found

    def _apple_window(self, pid: int, visible: bool, focus: bool = False) -> None:
        if platform.system() != "Darwin":
            return
        # The managed command is often a CLI launcher; prefer its GUI
        # descendants so the first activation targets an actual interface.
        ids = [*self._descendants(pid), pid]
        value = "true" if visible else "false"
        commands = "\n".join(
            f" try\n  tell (first process whose unix id is {child_pid}) to set visible to {value}\n"
            f"  {'set frontmost to true\n  try\n   perform action \"AXRaise\" of window 1\n  end try' if focus and visible and index == 0 else ''}\n"
            " end try"
            for index, child_pid in enumerate(ids)
        )
        script = f'tell application "System Events"\n{commands}\nend tell'
        with self._osascript_lock:
            try:
                subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass

    def _platform_window(self, pid: int, visible: bool, focus: bool = False) -> None:
        system = platform.system()
        if system == "Darwin":
            self._apple_window(pid, visible, focus)
        elif system == "Windows":
            self._windows_window(pid, visible, focus)
        elif system == "Linux":
            self._linux_window(pid, visible, focus)

    def _windows_window(self, pid: int, visible: bool, focus: bool = False) -> None:
        """Control top-level windows belonging to a Windows process via user32."""
        if platform.system() != "Windows":
            return
        user32 = ctypes.windll.user32
        show_state = 5 if visible else 0
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        focused = False

        @callback_type
        def visit(hwnd, _lparam):
            nonlocal focused
            owner_pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            if owner_pid.value == pid and user32.IsWindowVisible(hwnd) != visible:
                user32.ShowWindow(hwnd, show_state)
            if owner_pid.value == pid and focus and visible and not focused:
                user32.ShowWindow(hwnd, 5)
                user32.SetForegroundWindow(hwnd)
                focused = True
            return True

        user32.EnumWindows(visit, 0)

    def _linux_window(self, pid: int, visible: bool, focus: bool = False) -> None:
        """Use wmctrl, then xdotool, for X11 desktop window control."""
        if platform.system() != "Linux":
            return
        wmctrl = shutil.which("wmctrl")
        if wmctrl:
            try:
                output = subprocess.check_output([wmctrl, "-lp"], text=True, stderr=subprocess.DEVNULL)
                ids = [line.split()[0] for line in output.splitlines() if len(line.split()) >= 3 and line.split()[2] == str(pid)]
                for index, window_id in enumerate(ids):
                    state = "remove,hidden" if visible else "add,hidden"
                    subprocess.run([wmctrl, "-ir", window_id, "-b", state], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    # -ia activates and switches to the window's virtual desktop.
                    if focus and visible and index == 0:
                        subprocess.run([wmctrl, "-ia", window_id], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if ids:
                    return
            except (OSError, subprocess.SubprocessError):
                pass
        xdotool = shutil.which("xdotool")
        if not xdotool:
            return
        try:
            output = subprocess.check_output([xdotool, "search", "--pid", str(pid)], text=True, stderr=subprocess.DEVNULL)
            for index, window_id in enumerate(output.split()):
                command = "windowmap" if visible else "windowunmap"
                subprocess.run([xdotool, command, window_id], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if focus and visible and index == 0:
                    subprocess.run([xdotool, "windowactivate", "--sync", window_id], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass

    def set_visible(self, managed: ManagedProcess, visible: bool, focus: bool = False) -> None:
        if managed.process is None or not managed.running:
            return
        key = (id(managed), managed.process.pid)
        with self._visibility_lock:
            if self._last_visibility.get(key) == visible and not focus:
                return
            self._last_visibility[key] = visible
        # System Events/osascript can block while a renderer is creating its
        # windows. Queue it on one long-lived worker; never run it on Tk's
        # event thread and never create one thread per selection event.
        self._visibility_queue.put((managed, visible, focus))

    def mark_visible(self, managed: ManagedProcess, visible: bool) -> None:
        """Record visibility that the child GUI owns (automatic mode)."""
        if managed.process is not None and managed.running:
            with self._visibility_lock:
                self._last_visibility[(id(managed), managed.process.pid)] = visible

    def is_visible(self, managed: ManagedProcess) -> bool:
        if managed.process is None or not managed.running:
            return False
        with self._visibility_lock:
            return self._last_visibility.get((id(managed), managed.process.pid), False)


class ProcessManager:
    def __init__(self, root_dir: Path, on_event, lang: str = "zh"):
        prune_registry()
        self.root_dir = root_dir
        self.runtime = PlatformRuntime()
        self.on_event = on_event
        self.lang = lang
        self.groups: list[ProcessGroup] = [ProcessGroup("无分组", locked=True)]
        self.events: queue.Queue[tuple[str, ManagedProcess, str | None, int]] = queue.Queue()

    @property
    def items(self) -> list[ManagedProcess]:
        return [item for group in self.groups for item in group.items]

    def group_for(self, item: ManagedProcess) -> ProcessGroup:
        return next(group for group in self.groups if item in group.items)

    def add_group(self, name: str, index: int | None = None) -> ProcessGroup:
        group = ProcessGroup(name)
        if index is None:
            self.groups.append(group)
        else:
            self.groups.insert(index, group)
        return group

    def add(self, spec: ProcessSpec, group: ProcessGroup | None = None, index: int | None = None) -> ManagedProcess:
        if not spec.lsl_streams:
            spec.lsl_streams = scan_lsl_streams(spec.command, self.root_dir)
        item = ManagedProcess(spec)
        target_group = group or self.groups[0]
        if index is None:
            target_group.items.append(item)
        else:
            target_group.items.insert(index, item)
        return item

    def remove(self, item: ManagedProcess) -> bool:
        if item.running:
            return False
        self.group_for(item).items.remove(item)
        return True

    def remove_group(self, group: ProcessGroup, remove_items: bool) -> bool:
        if group.locked or group not in self.groups or any(item.running for item in group.items):
            return False
        if not remove_items:
            self.groups[0].items.extend(group.items)
        self.groups.remove(group)
        return True

    def start(self, item: ManagedProcess) -> None:
        if item.running:
            return
        self.root_dir.mkdir(parents=True, exist_ok=True)
        try:
            expanded = expand_macro_command(item.spec.command, [candidate.spec for candidate in self.items], self.root_dir, self.runtime.target)
            command = command_with_language(expanded, self.lang, self.runtime.target) if item.spec.supports_lang else translate_command(expanded, self.runtime.target)
        except ValueError as exc:
            item.lines.append(f"[launch error] {exc}")
            self.on_event("changed", item, None)
            return
        try:
            item.process = subprocess.Popen(
                self.runtime.translated_command_args(command),
                cwd=str(self.root_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=child_environment(),
                **self.runtime.popen_kwargs(),
            )
        except OSError as exc:
            item.lines.append(f"[launch error] {exc}")
            self.on_event("changed", item, None)
            return
        item.lines.clear()
        item.return_code = None
        item.stopping = False
        item.started_at = time.time()
        item.run_id += 1
        run_id = item.run_id
        process = item.process
        started_at = item.started_at
        register_process(build_entry(process.pid, item.spec.name, command, self.root_dir, item.spec.lsl_streams, started_at))
        item.reader = threading.Thread(target=self._read_output, args=(item, process, run_id), daemon=True)
        item.reader.start()
        item.waiter = threading.Thread(target=self._wait_for_exit, args=(item, process, run_id, started_at), daemon=True)
        item.waiter.start()
        self.on_event("changed", item, None)

    def _read_output(self, item: ManagedProcess, process: subprocess.Popen, run_id: int) -> None:
        stream = process.stdout
        if stream:
            for line in iter(stream.readline, ""):
                self.events.put(("line", item, line.rstrip("\r\n"), run_id))
            stream.close()

    def _wait_for_exit(self, item: ManagedProcess, process: subprocess.Popen, run_id: int, started_at: float) -> None:
        return_code = process.wait()
        if item.run_id == run_id:
            item.return_code = return_code
        self.events.put(("exit", item, str(return_code), run_id))
        unregister_process(process.pid, started_at=started_at)

    def stop(self, item: ManagedProcess) -> None:
        if not item.running or item.process is None or item.stopping:
            return
        item.stopping = True
        self.on_event("changed", item, None)
        threading.Thread(target=self._terminate_process_tree, args=(item,), daemon=True).start()

    def stop_all(self) -> None:
        for item in self.items:
            self.stop(item)

    def stop_all_and_wait(self, timeout: float = 4.0) -> bool:
        self.stop_all()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not any(item.running or item.stopping for item in self.items):
                return True
            time.sleep(0.05)
        return not any(item.running or item.stopping for item in self.items)

    def _process_snapshot(self) -> list[tuple[int, int, int]]:
        """Return pid/ppid/pgid; macOS ps does not expose a portable sid field."""
        if self.runtime.is_windows:
            return []
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,ppid=,pgid="], text=True)
            return [tuple(map(int, line.split())) for line in output.splitlines() if len(line.split()) == 3]
        except (OSError, subprocess.SubprocessError, ValueError):
            return []

    def _descendants(self, pid: int) -> list[int]:
        if self.runtime.is_windows:
            return []
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
            pairs = [tuple(map(int, line.split())) for line in output.splitlines() if line.split()]
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
        children: dict[int, list[int]] = {}
        for child, parent in pairs:
            children.setdefault(parent, []).append(child)
        found: list[int] = []
        pending = [pid]
        while pending:
            current = pending.pop()
            for child in children.get(current, []):
                found.append(child)
                pending.append(child)
        return found

    def _terminate_process_tree(self, item: ManagedProcess) -> None:
        process = item.process
        if process is None:
            item.stopping = False
            self.events.put(("changed", item, None, item.run_id))
            return
        pid = process.pid
        if self.runtime.is_windows:
            try:
                subprocess.run(
                    [os.environ.get("ComSpec", "cmd.exe"), "/c", "taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            finally:
                item.stopping = False
                self.events.put(("changed", item, None, item.run_id))
            return
        try:
            pgid = os.getpgid(pid)
        except OSError:
            item.stopping = False
            self.events.put(("changed", item, None, item.run_id))
            return
        snapshot = self._process_snapshot()
        owned = {pid, *self._descendants(pid)}
        group_members: dict[int, set[int]] = {}
        for child_pid, _ppid, child_pgid in snapshot:
            if child_pid in owned:
                group_members.setdefault(child_pgid, set()).add(child_pid)
        owned_groups = [group for group, members in group_members.items() if members]
        for group in owned_groups:
            try:
                os.killpg(group, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
        if process.poll() is None and not owned_groups:
            try:
                process.terminate()
            except OSError:
                pass
        deadline = time.monotonic() + 1.5
        def group_alive(group: int) -> bool:
            try:
                os.killpg(group, 0)
                return True
            except OSError:
                return False
        while time.monotonic() < deadline and (process.poll() is None or any(group_alive(group) for group in owned_groups)):
            time.sleep(0.05)
        if process.poll() is None or any(group_alive(group) for group in owned_groups):
            for group, members in group_members.items():
                try:
                    current = {entry[0] for entry in self._process_snapshot() if entry[2] == group}
                    if current and current.issubset(owned):
                        os.killpg(group, signal.SIGKILL)
                    else:
                        for child in members:
                            try:
                                os.kill(child, signal.SIGKILL)
                            except OSError:
                                pass
                except OSError:
                    continue
        item.stopping = False
        self.events.put(("changed", item, None, item.run_id))

    def drain_events(self) -> None:
        while True:
            try:
                kind, item, payload, run_id = self.events.get_nowait()
            except queue.Empty:
                return
            if run_id != item.run_id:
                continue
            if kind == "line" and payload is not None:
                item.lines.append(payload)
                item.lines[:] = item.lines[-4000:]
            self.on_event(kind, item, payload)


def shutil_available(name: str) -> bool:
    from shutil import which
    return which(name) is not None


class LanguageToggle(tk.Canvas):
    """Compact capsule switch without the platform Checkbutton background."""

    def __init__(self, parent, variable: tk.BooleanVar, command=None, **kwargs):
        super().__init__(parent, width=44, height=24, highlightthickness=0, bd=0, relief="flat", **kwargs)
        self.variable = variable
        self.command = command
        self._state = "normal"
        self.variable.trace_add("write", lambda *_args: self._draw())
        self.bind("<Button-1>", self._toggle)
        self.bind("<Key-space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", lambda _event: self._draw())
        self.bind("<FocusOut>", lambda _event: self._draw())
        self.configure(cursor="hand2", takefocus=True)
        self._draw()

    def _toggle(self, _event=None):
        if self._state == "disabled":
            return "break"
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()
        return "break"

    def configure(self, cnf=None, **kwargs):
        state = kwargs.pop("state", None)
        result = super().configure(cnf, **kwargs)
        if state is not None:
            self._state = state
            super().configure(cursor="arrow" if state == "disabled" else "hand2")
            self._draw()
        return result

    config = configure

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        enabled = bool(self.variable.get())
        disabled = self._state == "disabled"
        track = "#d8e0ea" if disabled else ("#0b72e7" if enabled else "#b8c4d2")
        knob = "#f8fafc" if disabled else "#ffffff"
        self.create_oval(1, 1, 23, 23, fill=track, outline=track)
        self.create_oval(21, 1, 43, 23, fill=track, outline=track)
        self.create_rectangle(12, 1, 32, 23, fill=track, outline=track)
        left = 3 if not enabled else 21
        self.create_oval(left, 3, left + 18, 21, fill=knob, outline="#cbd5e1" if disabled else knob, width=1)
        if self.focus_get() is self and not disabled:
            self.create_oval(0, 0, 44, 24, outline="#75b7ff", width=1)


class WorkflowDialog(tk.Toplevel):
    def __init__(self, parent, specs, initial: ProcessSpec | None = None, lang: str = "zh", root_dir: Path | None = None, target: str | None = None):
        super().__init__(parent)
        self.lang = lang
        self._t = lambda key, **kwargs: _text(self.lang, key, **kwargs)
        self.result: ProcessSpec | None = None
        self.specs = list(specs)
        self.root_dir = root_dir or Path.cwd()
        self.target = target
        self.plain_text = None
        self.plain_text_mode = False
        self._plain_text_stale = True
        self.title(self._t("workflow_title"))
        self.transient(parent)
        self.grab_set()
        body = ttk.Frame(self, padding=16)
        body.grid(sticky="nsew")
        ttk.Label(body, text=self._t("process_name")).grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=initial.localized_name(lang) if initial else "")
        ttk.Entry(body, textvariable=self.name_var, width=36).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 12))
        panes = ttk.Frame(body)
        panes.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 12))
        ttk.Label(panes, text=self._t("workflow_available")).grid(row=0, column=0, sticky="w")
        self.workflow_item_label = ttk.Label(panes, text=self._t("workflow_item"))
        self.workflow_item_label.grid(row=0, column=1, sticky="w", padx=(14, 0))
        self.available_items = tk.Listbox(panes, height=12, width=30, exportselection=False)
        self.available_items.grid(row=1, column=0, sticky="nsew")
        self.workflow_editor = ttk.Frame(panes, width=340, height=220)
        self.workflow_editor.grid(row=1, column=1, sticky="nsew", padx=(14, 0))
        self.workflow_editor.grid_propagate(False)
        self.workflow_editor.columnconfigure(0, weight=1)
        self.workflow_editor.rowconfigure(0, weight=1)
        self.items = tk.Listbox(self.workflow_editor, height=12, width=42, exportselection=False)
        self.items.grid(row=0, column=0, sticky="nsew")
        self.available_items.bind("<Double-1>", self._add_item)
        self.items.bind("<Double-1>", self._edit_reference_arguments)
        self._available_rows = [("sleep", None), *[("spec", spec) for spec in self.specs]]
        self.available_items.insert("end", "💤 等待一下" if lang == "zh" else "💤 Sleep for a While")
        for _kind, spec in self._available_rows[1:]:
            self.available_items.insert("end", spec.localized_name(lang))
        item_actions = ttk.Frame(body)
        item_actions.grid(row=3, column=1, sticky="e", pady=(0, 12))
        self.remove_button = ttk.Button(item_actions, text=self._t("remove_item"), command=self._remove_item)
        self.remove_button.pack(side="right")
        self.plain_text_button = ttk.Button(item_actions, text=self._t("plain_text_edit"), command=self._toggle_plain_text_edit)
        self.plain_text_button.pack(side="right", padx=(0, 8))
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text=self._t("cancel"), command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text=self._t("save"), command=self.save).pack(side="right")
        if initial:
            for line in initial.command.splitlines():
                if line.strip():
                    self.items.insert("end", line.strip())
        self.after_idle(self._present)

    def _present(self):
        self.lift(); self.focus_force()

    def _add_item(self, _event=None):
        selected = self.available_items.curselection()
        if not selected:
            return
        kind, spec = self._available_rows[selected[0]]
        if kind == "sleep":
            self.items.insert("end", "[sleep](1s)")
            self._plain_text_stale = True
            return
        if spec:
            self.items.insert("end", f"[{spec.localized_name(self.lang)}]()")
            self._plain_text_stale = True

    def _remove_item(self):
        selected = self.items.curselection()
        if selected:
            self.items.delete(selected[0])
            self._plain_text_stale = True

    def _toggle_plain_text_edit(self):
        if self.plain_text_mode:
            self._edit_as_workflow()
        else:
            self._edit_as_plain_text()

    def _edit_as_plain_text(self):
        if self._plain_text_stale:
            command = "\n".join(self.items.get(0, "end"))
            try:
                expanded = workflow_plain_text(command, self.specs, self.root_dir, self.target)
            except ValueError as exc:
                messagebox.showerror(self._t("workflow_title"), self._t("invalid_config", error=exc), parent=self)
                return
        self.items.grid_remove()
        if self.plain_text is None:
            self.plain_text = tk.Text(
                self.workflow_editor, height=1, width=1, wrap="none", undo=True,
                bg="#ffffff", fg="#243954", relief="flat", borderwidth=0,
                highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor="#0b72e7",
                font=("SFMono-Regular", 10), padx=8, pady=8,
            )
        if self._plain_text_stale:
            self.plain_text.delete("1.0", "end")
            self.plain_text.insert("1.0", expanded)
            self._plain_text_stale = False
        self.plain_text.grid(row=0, column=0, sticky="nsew")
        self.workflow_item_label.configure(text=self._t("terminal_commands"))
        self.available_items.configure(state="disabled")
        self.remove_button.configure(state="disabled")
        self.plain_text_button.configure(text=self._t("workflow_edit"))
        self.plain_text_mode = True
        self.plain_text.focus_set()

    def _edit_as_workflow(self):
        if self.plain_text is not None:
            self.plain_text.grid_remove()
        self.items.grid()
        self.workflow_item_label.configure(text=self._t("workflow_item"))
        self.available_items.configure(state="normal")
        self.remove_button.configure(state="normal")
        self.plain_text_button.configure(text=self._t("plain_text_edit"))
        self.plain_text_mode = False
        self.items.focus_set()

    def _edit_reference_arguments(self, event=None):
        index = self.items.nearest(event.y) if event is not None else (self.items.curselection()[0] if self.items.curselection() else None)
        if index is None:
            return
        value = self.items.get(index)
        parsed = parse_macro_line(value)
        bounds = self.items.bbox(index)
        if not parsed or not bounds:
            return
        title, arguments = parsed
        self.items.selection_clear(0, "end")
        self.items.selection_set(index)
        self.items.activate(index)
        row = tk.Frame(self.items, bg="#dcecff", highlightbackground="#0b72e7", highlightthickness=1, bd=0)
        prefix = tk.Label(row, text=f"[{title}](", bg="#dcecff", fg="#0a5fbe", bd=0, padx=1, pady=0)
        suffix = tk.Label(row, text=")", bg="#dcecff", fg="#0a5fbe", bd=0, padx=0, pady=0)
        argument_var = tk.StringVar(value=arguments)
        entry = tk.Entry(
            row, textvariable=argument_var, relief="flat", bd=0, highlightthickness=0,
            bg="#dcecff", fg="#0a5fbe", insertbackground="#0a5fbe", selectborderwidth=0,
        )
        prefix.pack(side="left")
        entry.pack(side="left", fill="y")
        suffix.pack(side="left")
        row.place(x=0, y=bounds[1], width=self.items.winfo_width(), height=bounds[3])
        measure_font = tkfont.Font(font=entry.cget("font"))
        def resize(*_args):
            width = max(measure_font.measure(argument_var.get() or " ") + 8, 18)
            entry.configure(width=max(1, len(argument_var.get()) + 1))
            entry.pack_configure(ipadx=max(0, (width - measure_font.measure(argument_var.get() or " ")) // 2))
        argument_var.trace_add("write", resize)
        resize()
        finished = False
        def finish(_event=None):
            nonlocal finished
            if finished:
                return "break"
            finished = True
            updated_arguments = argument_var.get().strip()
            try:
                if title.lower() == SLEEP_MACRO_TITLE:
                    parse_duration(updated_arguments)
                else:
                    _apply_macro_arguments("", updated_arguments)
            except ValueError:
                updated_arguments = arguments
            self.items.delete(index)
            self.items.insert(index, f"[{title}]({updated_arguments})")
            self.items.selection_set(index)
            self._plain_text_stale = True
            row.destroy()
            return "break"
        entry.bind("<Return>", finish)
        entry.bind("<FocusOut>", finish)
        entry.bind("<Escape>", lambda _event: (argument_var.set(arguments), finish())[1])
        entry.select_range(0, "end")
        entry.focus_set()

    def save(self):
        name = self.name_var.get().strip()
        command = self.plain_text.get("1.0", "end-1c").strip() if self.plain_text_mode else "\n".join(self.items.get(0, "end"))
        if not name or not command:
            messagebox.showwarning(self._t("incomplete"), self._t("invalid_workflow"), parent=self)
            return
        self.result = ProcessSpec(name, command, workflow=is_workflow_command(command))
        self.destroy()


class ProcessDialog(tk.Toplevel):
    def __init__(self, parent, initial: ProcessSpec | None = None, readonly: bool = False, lang: str = "zh", root_dir: Path | None = None, target: str | None = None):
        super().__init__(parent)
        self.lang = lang
        self._t = lambda key, **kwargs: _text(self.lang, key, **kwargs)
        self._escape_tag = f"ProcessDialogEscape{id(self)}"
        self.bind_class(self._escape_tag, "<KeyPress-Escape>", self._cancel)
        self.title(self._t("add_process_title") if initial is None else (self._t("process_config") if readonly else self._t("edit_process_title")))
        self.readonly = readonly
        self.root_dir = root_dir or Path.cwd()
        self.target = target
        self._supports_lang_touched = initial is not None and initial.supports_lang is not None
        self.resizable(False, False)
        self.result: ProcessSpec | None = None
        self.transient(parent)
        if isinstance(parent, tk.Toplevel):
            self.attributes("-topmost", True)
        self.grab_set()
        self.after_idle(self._present)
        body = ttk.Frame(self, padding=18)
        body.grid(sticky="nsew")
        ttk.Label(body, text=self._t("process_name")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        initial_name = initial.localized_name(lang) if initial else ""
        self.name_var = tk.StringVar(value=initial_name)
        self.name_entry = ttk.Entry(body, textvariable=self.name_var, width=42, state="readonly" if readonly else "normal")
        self.name_entry.grid(row=1, column=0, pady=(0, 12))
        ttk.Label(body, text=self._t("cli_command")).grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.command_text = tk.Text(body, width=48, height=6, wrap="none", bg="#f8fafc", fg="#243954", relief="solid", borderwidth=1, font=("SFMono-Regular", 10))
        self.command_text.grid(row=3, column=0, pady=(0, 14))
        if initial:
            self.command_text.insert("1.0", initial.command)
        if readonly:
            self.command_text.configure(state="disabled")
        self.supports_lang_var = tk.BooleanVar(value=bool(initial.supports_lang) if initial else False)
        language_row = tk.Frame(body, bg="#f4f7fb")
        language_row.grid(row=4, column=0, sticky="w", pady=(0, 8))
        self.supports_lang_toggle = LanguageToggle(
            language_row,
            self.supports_lang_var,
            command=lambda: setattr(self, "_supports_lang_touched", True),
            bg="#f4f7fb",
        )
        self.supports_lang_toggle.pack(side="left", padx=(0, 10))
        self.supports_lang_label = ttk.Label(language_row, text=self._t("supports_lang"), style="TLabel")
        self.supports_lang_label.pack(side="left")
        if readonly:
            self.supports_lang_toggle.configure(state="disabled")
        ttk.Label(body, text=self._t("newline_hint"), style="Muted.TLabel").grid(row=5, column=0, sticky="w", pady=(0, 8))
        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, sticky="e")
        cancel_button = ttk.Button(actions, text=self._t("close") if readonly else self._t("cancel"), command=self.destroy)
        cancel_button.pack(side="right", padx=(8, 0))
        focus_widgets = [body, self.name_entry, self.command_text, self.supports_lang_toggle, actions, cancel_button]
        if not readonly:
            save_button = ttk.Button(actions, text=self._t("save"), command=self.save)
            save_button.pack(side="right")
            focus_widgets.append(save_button)
        self.name_entry.bind("<Return>", self._confirm)
        self.command_text.bind("<Return>", self._confirm)
        self.command_text.bind("<Shift-Return>", self._insert_newline)
        self.name_entry.bind("<Tab>", self._focus_next)
        self.command_text.bind("<Tab>", self._focus_next)
        self.name_entry.bind("<Escape>", self._cancel)
        self.command_text.bind("<Escape>", self._cancel)
        self.bind("<Escape>", self._cancel)
        for widget in focus_widgets:
            tags = widget.bindtags()
            if self._escape_tag not in tags:
                widget.bindtags(tags + (self._escape_tag,))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
    def _present(self):
        if self.winfo_exists():
            self.lift()
            self.focus_force()

    def _insert_newline(self, _event):
        self.command_text.insert("insert", "\n")
        return "break"

    def _confirm(self, _event):
        self.save()
        return "break"

    def _cancel(self, _event):
        self.destroy()

    def _focus_next(self, _event):
        current = self.focus_get()
        if current is not None:
            next_widget = self.tk_focusNext(current)
            if next_widget.winfo_toplevel() == self:
                next_widget.focus_set()
                return "break"

    def save(self) -> None:
        if self.readonly:
            return
        name = self.name_var.get().strip()
        command = self.command_text.get("1.0", "end-1c").strip()
        if not name or not command:
            messagebox.showwarning(self._t("incomplete"), self._t("fill_required"), parent=self)
            return
        supports_lang = self.supports_lang_var.get()
        if not self._supports_lang_touched:
            supports_lang = probe_language_support(command, self.root_dir, self.target)
        self.result = ProcessSpec(name, command, supports_lang=supports_lang, workflow=is_workflow_command(command))
        self.destroy()


class GroupRemoveDialog(tk.Toplevel):
    def __init__(self, parent, group_count: int, lang: str = "zh"):
        super().__init__(parent)
        self._t = lambda key, **kwargs: _text(lang, key, **kwargs)
        self.result = "cancel"
        self.title(self._t("remove_group"))
        self.transient(parent)
        if isinstance(parent, tk.Toplevel):
            self.attributes("-topmost", True)
        self.grab_set(); self.resizable(False, False)
        body = ttk.Frame(self, padding=20); body.pack(fill="both", expand=True)
        ttk.Label(body, text=self._t("remove_group_question", count=group_count)).pack(anchor="w", pady=(0, 14))
        buttons = ttk.Frame(body); buttons.pack(anchor="e")
        def choose(value):
            self.result = value; self.destroy()
        ttk.Button(buttons, text=self._t("release"), command=lambda: choose("release")).pack(side="left")
        ttk.Button(buttons, text=self._t("no"), command=lambda: choose("cancel")).pack(side="left", padx=8)
        ttk.Button(buttons, text=self._t("yes"), command=lambda: choose("delete")).pack(side="left")
        self.bind("<Escape>", lambda _event: choose("cancel"))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.lang = "zh"
        self.title("集成终端控制台")
        self.title(self._t("app_title"))
        self.geometry("1180x720")
        self.minsize(900, 560)
        self.configure(bg="#f4f7fb")
        self._style()
        self.runtime = PlatformRuntime()
        self.root_dir = resolve_config_path(self._load_config().get("root_dir", str(APP_DIR)), self.runtime.target)
        self.manager = ProcessManager(self.root_dir, self._manager_event, self.lang)
        self.window_controller = WindowController(self)
        self.selected: ManagedProcess | None = None
        self.selected_items: list[ManagedProcess] = []
        self.selected_group: ProcessGroup | None = None
        self.selected_groups: list[ProcessGroup] = []
        self._drag_item: ManagedProcess | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._drag_active = False
        self._refreshing_tree = False
        self._log_render_id = 0
        self._log_refresh_pending = False
        self.process_list_window: tk.Toplevel | None = None
        self.process_list_tree: ttk.Treeview | None = None
        self.floating_start_button: RoundedButton | None = None
        self.floating_gui_button: RoundedButton | None = None
        self.floating_edit_button: RoundedButton | None = None
        self.floating_remove_button: RoundedButton | None = None
        self.process_terminal_window: tk.Toplevel | None = None
        self.process_terminal_text: tk.Text | None = None
        self.process_terminal_status: ttk.Label | None = None
        self._ui_widgets: dict[str, object] = {}
        self._floating_tree_map: dict[str, object] = {}
        self._build_ui()
        self._load_specs()
        self.after(100, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind_all("<Command-q>", self._command_quit)
        if platform.system() == "Darwin":
            try:
                self.tk.createcommand("tk::mac::Quit", self._close)
            except tk.TclError:
                pass

    def _t(self, key: str, **kwargs) -> str:
        """Translate a UI key, including for lightweight test doubles."""
        return _text(self.__dict__.get("lang", "zh"), key, **kwargs)

    def _item_name(self, item: ManagedProcess) -> str:
        return item.spec.localized_name(self.__dict__.get("lang", "zh"))

    def _group_name(self, group: ProcessGroup) -> str:
        return group.localized_name(self.__dict__.get("lang", "zh"))

    def _item_state_text(self, item: ManagedProcess) -> str:
        if item.stopping:
            return self._t("stopping")
        if item.running:
            return self._t("running")
        if item.return_code is not None:
            return f"{self._t('exited_state')} {item.return_code}"
        return self._t("idle")

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f7fb")
        style.configure("Panel.TFrame", background="#ffffff", relief="flat", borderwidth=0)
        style.configure("TLabel", background="#f4f7fb", foreground="#1d2b43", font=("SF Pro Display", 10))
        style.configure("Panel.TLabel", background="#ffffff", foreground="#1d2b43", font=("SF Pro Display", 10))
        style.configure("SectionTitle.TLabel", background="#ffffff", foreground="#1d2b43", font=("SF Pro Display", 13, "bold"))
        style.configure("TPanedwindow", background="#f4f7fb", borderwidth=0, relief="flat", sashwidth=8, sashpad=0, sashrelief="flat")
        style.configure("Title.TLabel", font=("SF Pro Display", 18, "bold"), foreground="#13213a")
        style.configure("Muted.TLabel", foreground="#647894")
        style.configure("TButton", padding=(12, 7), background="#eaf1f8", foreground="#243954", borderwidth=0, relief="flat")
        style.map("TButton", background=[("active", "#e7f1ff")], foreground=[("active", "#0b67d1")])
        style.configure("Accent.TButton", padding=(14, 8), background="#0b72e7", foreground="#ffffff", borderwidth=0, relief="flat")
        style.map("Accent.TButton", background=[("active", "#075bbd")], foreground=[("active", "#ffffff")])
        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground="#243954", rowheight=34, borderwidth=0, relief="flat", padding=0, lightcolor="#ffffff", darkcolor="#ffffff")
        style.configure("Treeview.Heading", background="#eef4fb", foreground="#5b708b", font=("SF Pro Display", 11, "bold"), borderwidth=0, relief="flat", padding=0, lightcolor="#eef4fb", darkcolor="#eef4fb")
        style.map("Treeview", background=[("selected", "#dcecff")], foreground=[("selected", "#0a5fbe")])

    def _toggle_language(self):
        self.lang = "en" if self.lang == "zh" else "zh"
        self.manager.lang = self.lang
        self.title(self._t("app_title"))
        for key, text_key in (("integrated_label", "integrated"), ("console_label", "console"),
                              ("root_button", "set_root"), ("import_button", "import_config"),
                              ("export_button", "export_config"), ("process_list_label", "process_list"),
                              ("add_button", "add_process"), ("workflow_button", "add_workflow"), ("group_button", "new_group"),
                              ("output_label", "output"), ("remove_button", "remove")):
            widget = self._ui_widgets.get(key)
            if widget is not None:
                widget.configure(text=f"  {self._t(text_key)}" if key == "console_label" else self._t(text_key))
        self.language_button.configure(text=self._t("language"))
        self.tree.heading("#0", text=self._t("name"))
        self.tree.heading("gui", text=self._t("gui"), anchor="center")
        self.tree.heading("state", text=self._t("state"))
        if self.process_list_tree is not None and self.process_list_tree.winfo_exists():
            self.process_list_tree.heading("gui", text=self._t("gui"), anchor="center")
            self.process_list_tree.heading("#0", text=self._t("name"), anchor="w")
            self.process_list_tree.heading("state", text=self._t("state"), anchor="w")
        self._refresh_process_list_window()
        if self.process_terminal_window is not None and self.process_terminal_window.winfo_exists():
            self.process_terminal_window.title(f"{self._t('integrated')} - {self._t('terminal_output')}")
        self._show_log(self.selected)
        self._refresh_tree()
        self._update_action_labels()

    def _build_ui(self):
        header = tk.Frame(self, bg="#f4f7fb", padx=24, pady=18)
        header.pack(fill="x")
        self._ui_widgets["integrated_label"] = ttk.Label(header, text=self._t("integrated"), style="Title.TLabel")
        self._ui_widgets["integrated_label"].pack(side="left")
        self._ui_widgets["console_label"] = ttk.Label(header, text=f"  {self._t('console')}", style="Muted.TLabel")
        self._ui_widgets["console_label"].pack(side="left", pady=(4, 0))
        ttk.Label(header, text=f"  {self.runtime.target}", style="Muted.TLabel").pack(side="left", pady=(4, 0))
        # RoundedButton(header, text="设置根目录", command=self._choose_root, width=112, icon="folder")
        self._ui_widgets["root_button"] = RoundedButton(header, text=self._t("set_root"), command=self._choose_root, width=112, icon="folder")
        self._ui_widgets["root_button"].pack(side="right")
        self._ui_widgets["import_button"] = RoundedButton(header, text=self._t("import_config"), command=self._import_config, width=98, icon="import")
        self._ui_widgets["import_button"].pack(side="right", padx=(0, 8))
        self._ui_widgets["export_button"] = RoundedButton(header, text=self._t("export_config"), command=self._export_config, width=98, icon="save")
        self._ui_widgets["export_button"].pack(side="right", padx=(0, 8))
        self.language_button = RoundedButton(header, text=self._t("language"), command=self._toggle_language, width=52)
        self.language_button.pack(side="right", padx=(0, 8))
        self.root_label = ttk.Label(header, text="", style="Muted.TLabel")
        self.root_label.pack(side="right", padx=(0, 14))
        self.root_label.configure(text=str(self.root_dir))

        split = ttk.Panedwindow(self, orient="horizontal")
        split.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        left = RoundedPanel(split)
        right = RoundedPanel(split)
        split.add(left, weight=1)
        split.add(right, weight=2)
        left_content, right_content = left.content, right.content
        top = tk.Frame(left_content, bg="#ffffff")
        top.pack(fill="x", pady=(0, 10))
        self._ui_widgets["process_list_label"] = ttk.Label(top, text=self._t("process_list"), style="SectionTitle.TLabel")
        self._ui_widgets["process_list_label"].pack(side="left")
        RoundedButton(top, text="", command=self._toggle_process_list_window, width=38, icon="pin").pack(side="right", padx=(6, 0))
        # RoundedButton(top, text="添加进程", command=self._add, width=108, icon="plus")
        self._ui_widgets["workflow_button"] = RoundedButton(top, text=self._t("add_workflow"), command=self._add_workflow, width=108, icon="plus")
        self._ui_widgets["workflow_button"].pack(side="right", padx=(6, 0))
        self._ui_widgets["add_button"] = RoundedButton(top, text=self._t("add_process"), command=self._add, width=108, icon="plus")
        self._ui_widgets["add_button"].pack(side="right", padx=(6, 0))
        # RoundedButton(top, text="新建分组", command=self._add_group, width=108, icon="plus")
        self._ui_widgets["group_button"] = RoundedButton(top, text=self._t("new_group"), command=self._add_group, width=108, icon="plus")
        self._ui_widgets["group_button"].pack(side="right")
        self.tree = ttk.Treeview(left_content, columns=("gui", "state", "pid"), show="tree headings", selectmode="extended")
        self._style_tree_arrows()
        self.tree.tag_configure("running", foreground="#18864b", background="#effaf3")
        self.tree.tag_configure("idle", foreground="#243954")
        self.tree.tag_configure("group", foreground="#1d5fa7", background="#eef5fc", font=("SF Pro Display", 12, "bold"))
        self.tree.heading("#0", text=self._t("name"), anchor="w")
        self.tree.heading("gui", text=self._t("gui"), anchor="center")
        self.tree.heading("state", text=self._t("state"), anchor="w")
        self.tree.heading("pid", text="PID", anchor="w")
        self.tree.column("#0", width=150, stretch=True)
        self.tree.column("gui", width=48, stretch=False, anchor="center")
        self.tree.column("state", width=90, stretch=False)
        self.tree.column("pid", width=75, stretch=False)
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Command-a>", self._select_all_processes)
        self.tree.bind("<Control-a>", self._select_all_processes)
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<Return>", lambda _event: self._start())
        self.tree.bind("<space>", self._edit_from_keyboard)
        self.tree.bind("<ButtonPress-1>", self._drag_start)
        self.tree.bind("<Button-1>", self._group_click, add="+")
        self.tree.bind("<B1-Motion>", self._drag_motion)
        self.tree.bind("<ButtonRelease-1>", self._drag_end)
        self.tree.bind("<<TreeviewOpen>>", self._group_opened)
        self.tree.bind("<<TreeviewClose>>", self._group_closed)
        actions = ttk.Frame(left_content, style="Panel.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        self.start_button = RoundedButton(actions, text=self._t("start"), command=self._toggle_processes, width=112, accent=True, icon="play", long_press_command=self._restart_processes)
        self.start_button.pack(side="left")
        self.gui_button = RoundedButton(actions, text=self._t("auto"), command=self._toggle_gui, width=112, icon="monitor")
        self.gui_button.pack(side="left", padx=6)
        self.edit_button = RoundedButton(actions, text=self._t("edit"), command=self._edit, width=82, icon="edit")
        self.edit_button.pack(side="right")
        self.remove_button = RoundedButton(actions, text=self._t("remove"), command=self._remove, width=82, icon="trash")
        self.remove_button.pack(side="right", padx=6)
        self._ui_widgets["remove_button"] = self.remove_button

        log_header = ttk.Frame(right_content, style="Panel.TFrame")
        log_header.pack(fill="x", pady=(0, 10))
        self._ui_widgets["output_label"] = ttk.Label(log_header, text=self._t("output"), style="SectionTitle.TLabel")
        self._ui_widgets["output_label"].pack(side="left")
        self.log_status = ttk.Label(log_header, text=self._t("no_process"), style="Panel.TLabel")
        self.log_status.pack(side="right")
        self.log = tk.Text(right_content, bg="#f8fafc", fg="#263a54", insertbackground="#0b72e7", relief="flat", borderwidth=0, highlightthickness=0, wrap="none", font=("SFMono-Regular", 10), padx=14, pady=14)
        self.log.tag_configure("bold", font=("SFMono-Regular", 10, "bold"))
        self.log.tag_configure("dim", foreground="#7d8da3")
        ansi_colors = {"color0": "#263a54", "color1": "#c0392b", "color2": "#20854a", "color3": "#a66a00", "color4": "#1769aa", "color5": "#8e44ad", "color6": "#087f8c", "color7": "#53657a"}
        for tag, color in ansi_colors.items():
            self.log.tag_configure(tag, foreground=color)
        self.log.pack(fill="both", expand=True, padx=4, pady=4)
        self.log.bind("<KeyPress>", self._log_keypress)
        footer = ttk.Frame(self, style="TFrame")
        footer.pack(fill="x", padx=24, pady=(0, 10))
        self.status = ttk.Label(footer, text=self._t("ready"), style="Muted.TLabel")
        self.status.pack(side="left")
        brand = ttk.Frame(footer, style="TFrame")
        brand.pack(side="right", anchor="e")
        ttk.Label(brand, text=f"v{VERSION_STRING}", style="Muted.TLabel", anchor="e").pack(side="left", padx=(0, 8))
        horizontal_brand(brand, background="#f4f7fb").pack(side="left")

    def _style_tree_arrows(self):
        """Replace the theme indicator with compact text chevrons."""
        style = ttk.Style(self)
        try:
            def replace(node):
                if isinstance(node, tuple):
                    name, options = node
                    options = dict(options)
                    if "children" in options:
                        children = []
                        for child in options["children"]:
                            if isinstance(child, tuple) and child and child[0] == "Treeitem.indicator":
                                continue
                            children.append(replace(child))
                        options["children"] = children
                    return (name, options)
                return node
            # Remove theme border wrappers as well as the native disclosure indicator.
            # Copy both layouts so the theme's native arrow is actually gone.
            style.layout("Pretty.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
            style.layout("Pretty.Treeview.Item", [replace(node) for node in style.layout("Treeview.Item")])
            # Let each column's heading(anchor=...) control text placement.
            # A west-sticky text element overrides the GUI column's centered
            # anchor on macOS's clam theme.
            style.layout("Pretty.Treeview.Heading", [("Treeheading.text", {"sticky": "nswe"}), ("Treeheading.image", {"side": "right", "sticky": ""})])
            self.tree.configure(style="Pretty.Treeview")
            style.configure("Pretty.Treeview", borderwidth=0, relief="flat", padding=0, lightcolor="#ffffff", darkcolor="#ffffff")
            style.configure("Pretty.Treeview.Heading", background="#eef4fb", foreground="#5b708b", font=("SF Pro Display", 11, "bold"), borderwidth=0, relief="flat", padding=0, lightcolor="#eef4fb", darkcolor="#eef4fb")
        except tk.TclError:
            pass

    def _toggle_process_list_window(self):
        if self.process_list_window is not None and self.process_list_window.winfo_exists():
            self._close_process_list_window()
            return
        window = tk.Toplevel(self)
        self.process_list_window = window
        window.title(f"{self._t('integrated')} - {self._t('process_list')}")
        window.geometry("560x520")
        window.minsize(420, 280)
        window.attributes("-topmost", True)
        window.configure(bg="#f4f7fb")
        panel = RoundedPanel(window, padding=10)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        frame = panel.content
        tree = ttk.Treeview(frame, columns=("gui", "state", "pid"), show="tree headings", selectmode="extended")
        tree.configure(style="Pretty.Treeview")
        tree.heading("#0", text=self._t("name"), anchor="w")
        tree.heading("gui", text=self._t("gui"), anchor="center")
        tree.heading("state", text=self._t("state"), anchor="w")
        tree.heading("pid", text="PID", anchor="w")
        tree.column("#0", width=220, stretch=True)
        tree.column("gui", width=48, stretch=False, anchor="center")
        tree.column("state", width=100, stretch=False)
        tree.column("pid", width=75, stretch=False)
        tree.tag_configure("running", foreground="#18864b", background="#effaf3")
        tree.tag_configure("idle", foreground="#243954")
        tree.tag_configure("group", foreground="#1d5fa7", background="#eef5fc", font=("SF Pro Display", 12, "bold"))
        tree.pack(fill="both", expand=True)
        actions = tk.Frame(frame, bg="#ffffff")
        actions.pack(fill="x", pady=(10, 0))
        self.floating_start_button = RoundedButton(actions, text=self._t("start"), command=self._toggle_processes, width=88, accent=True, icon="play", long_press_command=self._restart_processes)
        self.floating_start_button.pack(side="left")
        # RoundedButton(actions, text="自动调度", command=self._toggle_gui, width=88, icon="monitor")
        # self.floating_gui_button = RoundedButton(actions, text="自动调度", command=self._toggle_gui, width=88, icon="monitor")
        self.floating_gui_button = RoundedButton(actions, text=self._t("auto"), command=self._toggle_gui, width=88, icon="monitor")
        self.floating_gui_button.pack(side="left", padx=6)
        RoundedButton(actions, text=self._t("terminal"), command=self._toggle_process_terminal_window, width=78, icon="terminal").pack(side="left")
        self.floating_edit_button = RoundedButton(actions, text=self._t("edit"), command=self._edit, width=78, icon="edit")
        self.floating_edit_button.pack(side="right")
        self.floating_remove_button = RoundedButton(actions, text=self._t("remove"), command=self._remove, width=78, icon="trash")
        self.floating_remove_button.pack(side="right", padx=6)
        tree.bind("<<TreeviewSelect>>", self._floating_tree_selected)
        tree.bind("<Command-a>", self._select_all_processes)
        tree.bind("<Control-a>", self._select_all_processes)
        tree.bind("<Double-1>", self._floating_double_click)
        window.protocol("WM_DELETE_WINDOW", self._close_process_list_window)
        self.process_list_tree = tree
        self._refresh_process_list_window()
        self.withdraw()

    def _dialog_parent(self):
        """Keep dialogs attached to whichever console view is currently visible."""
        floating = self.__dict__.get("process_list_window")
        if floating is not None and floating.winfo_exists():
            return floating
        return self

    def _close_process_list_window(self):
        self._close_process_terminal_window()
        if self.process_list_window is not None and self.process_list_window.winfo_exists():
            self.process_list_window.destroy()
        self.process_list_window = None
        self.process_list_tree = None
        self.floating_start_button = None
        self.floating_gui_button = None
        self.floating_edit_button = None
        self.floating_remove_button = None
        self._floating_tree_map.clear()
        self.deiconify()
        self.lift()
        self.after_idle(self.focus_force)

    def _floating_tree_selected(self, _event=None):
        tree = self.process_list_tree
        if tree is None:
            return
        objects = [self._floating_tree_map[iid] for iid in tree.selection() if iid in self._floating_tree_map]
        focused = self._floating_tree_map.get(tree.focus())
        item_ids = [obj._tree_id for obj in objects if isinstance(obj, ManagedProcess) and hasattr(obj, "_tree_id")]
        group_ids = [obj._tree_id for obj in objects if isinstance(obj, ProcessGroup) and hasattr(obj, "_tree_id")]
        if item_ids or group_ids:
            if isinstance(focused, (ManagedProcess, ProcessGroup)) and hasattr(focused, "_tree_id"):
                self.tree.focus(focused._tree_id)
            self.tree.selection_set(group_ids + item_ids)
            self.after_idle(self._select)

    def _select_all_processes(self, event=None):
        """Select every process through the platform's standard Cmd/Ctrl+A."""
        tree = event.widget if event is not None and isinstance(event.widget, ttk.Treeview) else self.tree
        top_level_rows = tree.get_children("")
        if top_level_rows:
            tree.selection_set(top_level_rows)
            tree.focus(top_level_rows[0])
        if tree is self.process_list_tree:
            self._floating_tree_selected()
        else:
            self._select()
        return "break"

    def _floating_double_click(self, event):
        tree = self.process_list_tree
        if tree is None:
            return
        item = self._floating_tree_map.get(tree.identify_row(event.y))
        if not isinstance(item, ManagedProcess):
            return
        tree.selection_set(tree.identify_row(event.y))
        self._floating_tree_selected()
        self.manager.start(item)
        self._save_config()
        return "break"

    def _toggle_process_terminal_window(self):
        if self.process_terminal_window is not None and self.process_terminal_window.winfo_exists():
            self._close_process_terminal_window()
            return
        window = tk.Toplevel(self.process_list_window or self)
        self.process_terminal_window = window
        window.title(f"{self._t('integrated')} - {self._t('terminal_output')}")
        window.geometry("720x440")
        window.minsize(460, 260)
        window.attributes("-topmost", True)
        window.configure(bg="#f4f7fb")
        panel = RoundedPanel(window, padding=10)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        self.process_terminal_status = ttk.Label(panel.content, text=self._t("no_process"), style="SectionTitle.TLabel")
        self.process_terminal_status.pack(fill="x", pady=(0, 8))
        self.process_terminal_text = tk.Text(panel.content, bg="#f8fafc", fg="#263a54", relief="flat", borderwidth=0, highlightthickness=0, wrap="none", font=("SFMono-Regular", 10), padx=14, pady=14)
        self.process_terminal_text.pack(fill="both", expand=True)
        self.process_terminal_text.bind("<KeyPress>", self._log_keypress)
        window.protocol("WM_DELETE_WINDOW", self._close_process_terminal_window)
        self._refresh_process_terminal()

    def _close_process_terminal_window(self):
        if self.process_terminal_window is not None and self.process_terminal_window.winfo_exists():
            self.process_terminal_window.destroy()
        self.process_terminal_window = None
        self.process_terminal_text = None
        self.process_terminal_status = None

    def _refresh_process_terminal(self):
        output = self.process_terminal_text
        status = self.process_terminal_status
        if output is None or status is None or not output.winfo_exists():
            return
        output.configure(state="normal")
        output.delete("1.0", "end")
        if self.selected is None:
            status.configure(text=self._t("no_process"))
        else:
            suffix = self._t("running") if self.selected.running else self._t("stopped")
            status.configure(text=f"{self._item_name(self.selected)}  /  {len(self.selected.lines)} {self._t('lines')} / {suffix}")
            output.insert("end", "\n".join(clean_terminal_text(line) for line in self.selected.lines[-1200:]))
            output.see("end")
        # Keep the pinned terminal readable from the beginning of long lines.
        output.xview_moveto(0)
        output.configure(state="disabled")

    def _refresh_process_list_window(self):
        # Keep stable row IDs so a process exit cannot leave a partially rebuilt suffix.
        tree = self.process_list_tree
        if tree is None or not tree.winfo_exists():
            return
        self._floating_tree_map.clear()
        desired = set()
        for group_index, group in enumerate(self.manager.groups):
            running = any(item.running for item in group.items)
            marker = "▾" if group.expanded else "▸"
            group_iid = f"group-{id(group)}"
            desired.add(group_iid)
            if tree.exists(group_iid):
                tree.item(group_iid, text=f"    {marker}  {self._group_name(group)}", values=("", self._t("running") if running else self._t("idle"), ""), open=group.expanded, tags=("running" if running else "idle", "group"))
                tree.move(group_iid, "", group_index)
            else:
                tree.insert("", group_index, iid=group_iid, text=f"    {marker}  {self._group_name(group)}", values=("", self._t("running") if running else self._t("idle"), ""), open=group.expanded, tags=("running" if running else "idle", "group"))
            self._floating_tree_map[group_iid] = group
            for item_index, item in enumerate(group.items):
                state = self._item_state_text(item)
                pid = str(item.process.pid) if item.process and item.running else "-"
                iid = f"item-{id(item)}"
                desired.add(iid)
                values = ({"auto": "◩", "show": "▣", "hide": "▢"}[item.gui_mode], state, pid)
                if tree.exists(iid):
                    tree.item(iid, text=self._item_name(item), values=values, tags=("running" if item.running else "idle",))
                    tree.move(iid, group_iid, item_index)
                else:
                    tree.insert(group_iid, item_index, iid=iid, text=self._item_name(item), values=values, tags=("running" if item.running else "idle",))
                self._floating_tree_map[iid] = item
        stale = []
        for group_iid in tree.get_children(""):
            stale.append(group_iid)
            stale.extend(tree.get_children(group_iid))
        for iid in stale:
            if iid not in desired and tree.exists(iid):
                tree.delete(iid)

    def _item_gui_visible(self, item: ManagedProcess) -> bool:
        return self.window_controller.is_visible(item)

    def _desired_gui_visibility(self, item: ManagedProcess) -> bool:
        if item.gui_mode == "show":
            return True
        if item.gui_mode == "hide":
            return False
        return item in self.selected_items and item.running

    def _sync_gui_visibility(self, focus_item: ManagedProcess | None = None):
        for item in self.manager.items:
            if item.running:
                visible = self._desired_gui_visibility(item)
                self.window_controller.set_visible(item, visible, focus=visible and item is focus_item)

    def _focus_selected_window(self, item: ManagedProcess):
        """Retry focus after GUI descendants have had time to create a window."""
        if item.running and item in self.selected_items and self._desired_gui_visibility(item):
            self.window_controller.set_visible(item, True, focus=True)

    def _group_opened(self, _event=None):
        if self._refreshing_tree:
            return
        self._schedule_group_expanded_sync()

    def _group_closed(self, _event=None):
        if self._refreshing_tree:
            return
        self._schedule_group_expanded_sync()

    def _schedule_group_expanded_sync(self):
        iid = self.tree.focus()
        self.after_idle(lambda: self._sync_group_expanded_from_tree(iid))

    def _sync_group_expanded_from_tree(self, iid: str):
        if not iid or not self.tree.exists(iid):
            return
        group = next((group for group in self.manager.groups if getattr(group, "_tree_id", None) == iid), None)
        if group is not None:
            expanded = bool(self.tree.item(iid, "open"))
            if group.expanded == expanded:
                return
            group.expanded = expanded
            self._save_config()

    def _group_click(self, event):
        row = self.tree.identify_row(event.y)
        group = next((candidate for candidate in self.manager.groups if getattr(candidate, "_tree_id", None) == row), None)
        if group is None:
            return
        box = self.tree.bbox(group._tree_id, "#0")
        if box and event.x <= box[0] + 28:
            group.expanded = not group.expanded
            self._save_config()
            self._refresh_tree()
            return "break"

    def _config_data(self):
        return {
            "root_dir": str(self.root_dir),
            "groups": [{"name": group.name, "name_zh": group.name_zh, "name_en": group.name_en, "locked": group.locked, "expanded": group.expanded,
                        "processes": [{"name": item.spec.name, "name_zh": item.spec.name_zh, "name_en": item.spec.name_en,
                                       "command": item.spec.command, "enabled": item.spec.enabled,
                                       "supports_lang": item.spec.supports_lang,
                                       "workflow": item.spec.workflow if item.spec.workflow is not None else is_workflow_command(item.spec.command),
                                       "gui_mode": item.gui_mode, "lsl_streams": item.spec.lsl_streams} for item in group.items]}
                       for group in self.manager.groups],
        }

    def _load_config(self):
        source = CONFIG_PATH if CONFIG_PATH.exists() else BUNDLED_CONFIG_PATH
        try:
            return json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _validate_candidate(self, candidate: ProcessSpec | None = None, replacing: ManagedProcess | None = None) -> bool:
        specs = [item.spec for item in self.manager.items if item is not replacing]
        if candidate is not None:
            specs.append(candidate)
        try:
            validate_macro_cycles(specs)
        except ValueError as exc:
            messagebox.showerror(self._t("invalid_config"), str(exc), parent=self._dialog_parent())
            return False
        return True

    def _save_config(self):
        if not self._validate_candidate():
            return False
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self._config_data(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True

    def _export_config(self):
        target = filedialog.asksaveasfilename(
            title=self._t("export_config"),
            initialdir=str(self.root_dir),
            initialfile="bci-device-config.json",
            defaultextension=".json",
            filetypes=[(self._t("json_config"), "*.json"), (self._t("all_files"), "*")],
        )
        if not target:
            return
        try:
            Path(target).write_text(json.dumps(self._config_data(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(self._t("save_failed"), self._t("cannot_save", error=exc), parent=self)
            return
        self.status.configure(text=self._t("saved", target=target))

    def _import_config(self):
        source = filedialog.askopenfilename(
            title=self._t("import_config"),
            initialdir=str(self.root_dir),
            filetypes=[(self._t("json_config"), "*.json"), (self._t("all_files"), "*")],
        )
        if not source:
            return
        try:
            raw = json.loads(Path(source).read_text(encoding="utf-8"))
            root_dir = resolve_config_path(raw.get("root_dir", str(self.root_dir)), self.runtime.target)
            groups = self._parse_groups(raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            messagebox.showerror(self._t("import_failed"), self._t("invalid_config", error=exc), parent=self)
            return
        self.manager.stop_all_and_wait()
        self.manager.groups = groups
        self.root_dir = root_dir
        self.manager.root_dir = root_dir
        self.selected = None
        self.selected_items = []
        self.selected_group = None
        self.root_label.configure(text=str(self.root_dir))
        self._save_config()
        self._refresh_tree()
        self._show_log(None)
        self.status.configure(text=self._t("imported", items=len(self.manager.items), groups=len(self.manager.groups)))

    def _parse_groups(self, raw):
        groups = []
        source_groups = raw.get("groups")
        if source_groups is None:
            source_groups = [{"name": "无分组", "locked": True, "processes": raw.get("processes", [])}]
        for index, group_raw in enumerate(source_groups):
            if not isinstance(group_raw, dict):
                raise ValueError("分组必须是对象")
            name = str(group_raw.get("name", "")).strip()
            if name == "无分组":
                name, locked = "无分组", True
            elif not name:
                raise ValueError("分组名称不能为空")
            else:
                locked = bool(group_raw.get("locked", False))
            name_zh = str(group_raw.get("name_zh") or "").strip() or None
            name_en = str(group_raw.get("name_en") or "").strip() or None
            group = ProcessGroup(name, locked=locked, expanded=bool(group_raw.get("expanded", True)), name_zh=name_zh, name_en=name_en)
            for entry in group_raw.get("processes", []):
                if not isinstance(entry, dict) or not str(entry.get("name", "")).strip() or not str(entry.get("command", "")).strip():
                    raise ValueError("每个进程必须包含非空的 name 和 command")
                gui_mode = str(entry.get("gui_mode", "auto")).strip().lower()
                if gui_mode not in {"auto", "show", "hide"}:
                    gui_mode = "auto"
                streams = list(entry.get("lsl_streams", [])) if isinstance(entry.get("lsl_streams", []), list) else []
                if not streams:
                    streams = scan_lsl_streams(str(entry["command"]), self.__dict__.get("root_dir", Path.cwd()))
                legacy_name = str(entry["name"]).strip()
                name_zh = str(entry.get("name_zh") or "").strip() or None
                name_en = str(entry.get("name_en") or "").strip() or None
                group.items.append(ManagedProcess(
                    ProcessSpec(
                        legacy_name,
                        str(entry["command"]).strip(),
                        bool(entry.get("enabled", True)),
                        streams,
                        name_zh,
                        name_en,
                        entry.get("supports_lang") if isinstance(entry.get("supports_lang"), bool) else None,
                        entry.get("workflow") if isinstance(entry.get("workflow"), bool) else is_workflow_command(str(entry["command"])),
                    ),
                    gui_mode=gui_mode,
                ))
            if group.name == "无分组":
                group.name_zh = group.name_zh or "无分组"
                group.name_en = group.name_en or "Ungrouped"
                existing = next((candidate for candidate in groups if candidate.name == "无分组"), None)
                if existing is None:
                    groups.append(group)
                else:
                    existing.items.extend(group.items)
                    existing.expanded = existing.expanded or group.expanded
            else:
                groups.append(group)
        if not any(group.name == "无分组" for group in groups):
            groups.append(ProcessGroup("无分组", locked=True, name_zh="无分组", name_en="Ungrouped"))
        return groups

    def _command_quit(self, _event=None):
        self._close()
        return "break"

    def _load_specs(self):
        self.manager.groups = self._parse_groups(self._load_config())
        self._save_config()
        self._refresh_tree()

    def _refresh_tree(self):
        # Update rows in place. Rebuilding the whole tree during an exit event can
        # interleave with native Treeview callbacks and truncate the visible suffix.
        selected_items = list(self.selected_items)
        selected_group = self.selected_group
        selected_groups = list(self.selected_groups)
        self._refreshing_tree = True
        try:
            desired = set()
            for group_index, group in enumerate(self.manager.groups):
                running = any(item.running for item in group.items)
                marker = "▾" if group.expanded else "▸"
                group_iid = f"group-{id(group)}"
                desired.add(group_iid)
                group_values = ("", self._t("running") if running else self._t("idle"), "")
                if self.tree.exists(group_iid):
                    self.tree.item(group_iid, text=f"    {marker}  {self._group_name(group)}", values=group_values, open=group.expanded, tags=("running" if running else "idle", "group"))
                    self.tree.move(group_iid, "", group_index)
                else:
                    self.tree.insert("", group_index, iid=group_iid, text=f"    {marker}  {self._group_name(group)}", values=group_values, open=group.expanded, tags=("running" if running else "idle", "group"))
                group._tree_id = group_iid
                for item_index, item in enumerate(group.items):
                    state = self._item_state_text(item)
                    pid = str(item.process.pid) if item.process and item.running else "-"
                    gui_icon = {"auto": "◩", "show": "▣", "hide": "▢"}[item.gui_mode]
                    iid = f"item-{id(item)}"
                    desired.add(iid)
                    values = (gui_icon, state, pid)
                    if self.tree.exists(iid):
                        self.tree.item(iid, text=self._item_name(item), values=values, tags=("running" if item.running else "idle",))
                        self.tree.move(iid, group_iid, item_index)
                    else:
                        self.tree.insert(group_iid, item_index, iid=iid, text=self._item_name(item), values=values, tags=("running" if item.running else "idle",))
                    item._tree_id = iid
            stale = []
            for group_iid in self.tree.get_children(""):
                stale.append(group_iid)
                stale.extend(self.tree.get_children(group_iid))
            for iid in stale:
                if iid not in desired and self.tree.exists(iid):
                    self.tree.delete(iid)
            valid = [item for item in selected_items if item in self.manager.items]
            self.selected_items = valid
            valid_groups = [group for group in selected_groups if group in self.manager.groups]
            self.selected_groups = valid_groups
            self.selected_group = valid_groups[0] if valid_groups else None
            selection_ids = [group._tree_id for group in valid_groups]
            selection_ids.extend(item._tree_id for item in valid if hasattr(item, "_tree_id"))
            if selection_ids:
                self.tree.selection_set(selection_ids)
            self._update_action_labels()
        finally:
            self._refreshing_tree = False
        self._refresh_process_list_window()

    def _choose_root(self):
        selected = filedialog.askdirectory(initialdir=str(self.root_dir), title=self._t("set_root"))
        if selected:
            self.root_dir = Path(selected)
            self.manager.root_dir = self.root_dir
            self.root_label.configure(text=str(self.root_dir))
            self._save_config()
            self.status.configure(text=self._t("root_changed", root=self.root_dir))

    def _add(self):
        dialog = ProcessDialog(self._dialog_parent(), lang=self.lang, root_dir=self.root_dir, target=self.runtime.target)
        self.wait_window(dialog)
        if dialog.result:
            if not self._validate_candidate(dialog.result):
                return
            if self.lang == "zh":
                dialog.result.name_zh = dialog.result.name
            else:
                dialog.result.name_en = dialog.result.name
            dialog.result.lsl_streams = scan_lsl_streams(dialog.result.command, self.root_dir)
            anchor = self._selection_anchor()
            if isinstance(anchor, ManagedProcess):
                group = self.manager.group_for(anchor)
                self.manager.add(dialog.result, group, group.items.index(anchor) + 1)
            elif isinstance(anchor, ProcessGroup):
                self.manager.add(dialog.result, anchor)
            else:
                self.manager.add(dialog.result)
            self._save_config(); self._refresh_tree()

    def _add_workflow(self):
        dialog = WorkflowDialog(self._dialog_parent(), [item.spec for item in self.manager.items], lang=self.lang, root_dir=self.root_dir, target=self.runtime.target)
        self.wait_window(dialog)
        if not dialog.result or not self._validate_candidate(dialog.result):
            return
        if self.lang == "zh":
            dialog.result.name_zh = dialog.result.name
        else:
            dialog.result.name_en = dialog.result.name
        anchor = self._selection_anchor()
        group = self.manager.group_for(anchor) if isinstance(anchor, ManagedProcess) else anchor if isinstance(anchor, ProcessGroup) else None
        index = group.items.index(anchor) + 1 if isinstance(anchor, ManagedProcess) else None
        self.manager.add(dialog.result, group, index)
        self._save_config(); self._refresh_tree()

    def _add_group(self):
        anchor = self._selection_anchor()
        if isinstance(anchor, ManagedProcess):
            anchor_group = self.manager.group_for(anchor)
        elif isinstance(anchor, ProcessGroup):
            anchor_group = anchor
        else:
            anchor_group = None
        insert_at = self.manager.groups.index(anchor_group) + 1 if anchor_group is not None else None
        group = self.manager.add_group("新分组", insert_at)
        self.selected_group = group
        self.selected_groups = [group]
        self.selected = None
        self.selected_items = []
        self._save_config()
        self._refresh_tree()
        self.tree.selection_set(group._tree_id)
        self.tree.see(group._tree_id)
        self.after_idle(lambda: self._begin_group_rename(group))

    def _selection_anchor(self):
        """Return the last-focused process/group as the insertion anchor."""
        focused_id = self.tree.focus()
        for group in self.manager.groups:
            if getattr(group, "_tree_id", None) == focused_id:
                return group
            for item in group.items:
                if getattr(item, "_tree_id", None) == focused_id:
                    return item
        if self.selected is not None and self.selected in self.manager.items:
            return self.selected
        if self.selected_group is not None and self.selected_group in self.manager.groups:
            return self.selected_group
        return None

    def _edit(self):
        if self.selected_groups:
            return
        if len(self.selected_items) != 1:
            return
        self.selected = self.selected_items[0]
        readonly = self.selected.running
        if not readonly and (self.selected.spec.workflow or is_workflow_command(self.selected.spec.command)):
            dialog = WorkflowDialog(self._dialog_parent(), [item.spec for item in self.manager.items if item is not self.selected], self.selected.spec, lang=self.lang, root_dir=self.root_dir, target=self.runtime.target)
            self.wait_window(dialog)
            if not dialog.result or not self._validate_candidate(dialog.result, replacing=self.selected):
                return
            edited_name = dialog.result.name
            previous_spec = self.selected.spec
            dialog.result.name = previous_spec.name
            dialog.result.name_zh = previous_spec.name_zh
            dialog.result.name_en = previous_spec.name_en
            self.selected.spec = dialog.result
            if self.lang == "zh":
                self.selected.spec.name_zh = edited_name
            else:
                self.selected.spec.name_en = edited_name
            self._save_config(); self._refresh_tree()
            return
        dialog = ProcessDialog(self._dialog_parent(), self.selected.spec, readonly=readonly, lang=self.lang, root_dir=self.root_dir, target=self.runtime.target)
        self.wait_window(dialog)
        if not readonly and dialog.result:
            if not self._validate_candidate(dialog.result, replacing=self.selected):
                return
            dialog.result.lsl_streams = scan_lsl_streams(dialog.result.command, self.root_dir)
            edited_name = dialog.result.name
            previous_spec = self.selected.spec
            dialog.result.name = previous_spec.name
            dialog.result.name_zh = previous_spec.name_zh
            dialog.result.name_en = previous_spec.name_en
            self.selected.spec = dialog.result
            if self.lang == "zh":
                self.selected.spec.name_zh = edited_name
            else:
                self.selected.spec.name_en = edited_name
            self._save_config(); self._refresh_tree()

    def _edit_from_keyboard(self, _event=None):
        if self.selected_groups or len(self.selected_items) != 1:
            return "break"
        self._edit()
        return "break"

    def _remove(self):
        if self.selected_groups:
            groups = [group for group in self.selected_groups if not group.locked]
            if not groups:
                return
            if any(item.running for group in groups for item in group.items):
                messagebox.showwarning(self._t("cannot_remove_group"), self._t("stop_group_first"), parent=self._dialog_parent())
                return
            dialog = GroupRemoveDialog(self._dialog_parent(), len(groups), lang=self.lang); self.wait_window(dialog)
            if dialog.result == "cancel":
                return
            for group in groups:
                self.manager.remove_group(group, remove_items=dialog.result == "delete")
            self.selected_group = None; self.selected_groups = []; self.selected_items = []
            self._save_config(); self._refresh_tree(); self._show_log(None)
            return
        removable = [item for item in self.selected_items if not item.running]
        if not removable or not messagebox.askyesno(self._t("remove_process"), self._t("confirm_remove", count=len(removable)), parent=self._dialog_parent()):
            return
        removed = False
        for item in removable:
            removed = self.manager.remove(item) or removed
        if removed:
            self.selected = None; self.selected_items = []; self._save_config(); self._refresh_tree(); self._show_log(None)

    def _select(self, _event=None):
        if self._refreshing_tree:
            return
        selected = self.tree.selection()
        previous = self.selected
        previous_group = self.selected_group
        selected_groups = [group for group in self.manager.groups if getattr(group, "_tree_id", None) in selected]
        selected_group = selected_groups[0] if selected_groups else None
        new_items = []
        for group in selected_groups:
            new_items.extend(group.items)
        new_items.extend(item for item in self.manager.items if getattr(item, "_tree_id", None) in selected and item not in new_items)
        focused_item = next((item for item in new_items if getattr(item, "_tree_id", None) == self.tree.focus()), None)
        new_primary = focused_item or (previous if previous in new_items else (new_items[-1] if new_items else None))
        self.selected_group = selected_group
        self.selected_groups = selected_groups
        self.selected_items = new_items
        self.selected = new_primary
        self._sync_gui_visibility(focus_item=self.selected)
        self._show_log(self.selected)
        self._update_action_labels()
        # Selecting an idle process must be a pure selection/UI operation.
        # Avoid rebuilding the Treeview here: rebuilding it re-emits selection
        # events and can re-enter this handler while Tk is dispatching input.
        # Do not rebuild the tree from a selection callback. The tree rebuild
        # itself emits another selection event and is the source of re-entry
        # under macOS when a renderer is starting.
        for candidate in self.manager.items:
            iid = getattr(candidate, "_tree_id", None)
            if iid and self.tree.exists(iid):
                values = self.tree.item(iid, "values")
                self.tree.item(iid, values=({"auto": "◩", "show": "▣", "hide": "▢"}[candidate.gui_mode], *values[1:]))

    def _double_click(self, event):
        row = self.tree.identify_row(event.y)
        group = next((candidate for candidate in self.manager.groups if getattr(candidate, "_tree_id", None) == row), None)
        if group is not None:
            if not group.locked:
                # Wait until the double-click and its button-release have
                # finished before creating the IME target widget.
                self.after(80, lambda: self._begin_group_rename(group))
            return "break"
        item = next((candidate for candidate in self.manager.items if getattr(candidate, "_tree_id", None) == row), None)
        if item is None:
            return
        self.tree.selection_set(item._tree_id)
        self.selected_items = [item]
        self.selected = item
        self.manager.start(item)
        self._save_config()

    def _begin_group_rename(self, group: ProcessGroup):
        if group.locked or not hasattr(group, "_tree_id"):
            return
        bbox = self.tree.bbox(group._tree_id, "#0")
        if not bbox:
            return
        x, y, width, height = bbox
        editor = ttk.Entry(self.tree)
        editor.insert(0, self._group_name(group))
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=max(width, 80), height=height)
        # Hand focus to the IME exactly once. Re-focusing or changing the
        # selection after composition starts commits the first pinyin key as
        # an ASCII character on Aqua/Tk.
        self.after_idle(editor.focus_set)
        finished = False
        def finish(_event=None):
            nonlocal finished
            if finished:
                return "break"
            finished = True
            name = editor.get().strip()
            if name:
                if self.lang == "zh":
                    group.name_zh = name
                else:
                    group.name_en = name
            editor.destroy()
            self._save_config(); self._refresh_tree()
            return "break"
        editor.bind("<Return>", finish)
        editor.bind("<FocusOut>", finish)
        editor.bind("<Escape>", lambda _event: (editor.destroy(), self._refresh_tree(), "break")[-1])

    def _update_action_labels(self):
        if not hasattr(self, "start_button"):
            return
        if self.selected_groups:
            any_running = any(item.running for item in self.selected_items)
            start_text = self._t("stop_group") if any_running else self._t("start_group")
            start_icon = "stop" if any_running else "play"
        elif len(self.selected_items) > 1:
            any_running = any(item.running for item in self.selected_items)
            start_text = self._t("stop_all") if any_running else self._t("start_all")
            start_icon = "stop" if any_running else "play"
        elif self.selected:
            start_text = self._t("stop") if self.selected.running else self._t("start")
            start_icon = "stop" if self.selected.running else "play"
        else:
            start_text, start_icon = self._t("start"), "play"
        # restart_text = "全部重启" if self.selected_groups or len(self.selected_items) > 1 else "重启"
        restart_text = self._t("restart_all") if self.selected_groups or len(self.selected_items) > 1 else self._t("restart")
        self.start_button.configure(text=start_text, icon=start_icon, long_press_text=restart_text)
        if self.floating_start_button is not None and self.floating_start_button.winfo_exists():
            self.floating_start_button.configure(text=start_text, icon=start_icon, long_press_text=restart_text)
        # A stopped process has no controllable windows; keep the control in a
        # predictable disabled "show" state until it is running again.
        can_control_gui = bool(self.selected_items and any(item.running for item in self.selected_items))
        selected_modes = {item.gui_mode for item in self.selected_items}
        gui_mode = self.selected.gui_mode if self.selected and len(selected_modes) != 1 else (next(iter(selected_modes)) if len(selected_modes) == 1 else "auto")
        # labels = {"auto": ("自动调度", "monitor"), "show": ("保持显示", "monitor"), "hide": ("保持隐藏", "monitor")}
        labels = {"auto": (self._t("auto"), "monitor"), "show": (self._t("show"), "monitor"), "hide": (self._t("hide"), "monitor")}
        gui_text, gui_icon = labels[gui_mode]
        self.gui_button.configure(text=gui_text, icon=gui_icon)
        if self.floating_gui_button is not None and self.floating_gui_button.winfo_exists():
            self.floating_gui_button.configure(text=gui_text, icon=gui_icon)
        self.gui_button.configure(state="normal" if can_control_gui else "disabled")
        if self.floating_gui_button is not None and self.floating_gui_button.winfo_exists():
            self.floating_gui_button.configure(state="normal" if can_control_gui else "disabled")
        can_edit = not self.selected_groups and len(self.selected_items) == 1
        self.edit_button.configure(text=self._t("configure") if can_edit and self.selected_items[0].running else self._t("edit"), icon="monitor" if can_edit and self.selected_items[0].running else "edit")
        self.edit_button.configure(state="normal" if can_edit else "disabled")
        if self.floating_edit_button is not None and self.floating_edit_button.winfo_exists():
            # floating_edit_text = "配置" if can_edit and self.selected_items[0].running else "编辑"
            floating_edit_text = self._t("configure") if can_edit and self.selected_items[0].running else self._t("edit")
            floating_edit_icon = "monitor" if can_edit and self.selected_items[0].running else "edit"
            self.floating_edit_button.configure(text=floating_edit_text, icon=floating_edit_icon, state="normal" if can_edit else "disabled")
        can_remove = (bool(self.selected_groups) and any(not group.locked for group in self.selected_groups)) or bool(self.selected_items)
        self.remove_button.configure(state="normal" if can_remove else "disabled")
        if getattr(self, "floating_remove_button", None) is not None and self.floating_remove_button.winfo_exists():
            self.floating_remove_button.configure(text=self._t("remove"), state="normal" if can_remove else "disabled")

    def _drag_start(self, event):
        row = self.tree.identify_row(event.y)
        self._drag_group = next((group for group in self.manager.groups if getattr(group, "_tree_id", None) == row), None)
        self._drag_item = next((item for item in self.manager.items if getattr(item, "_tree_id", None) == row), None)
        self._drag_origin = (event.x, event.y)
        self._drag_active = False

    def _drag_motion(self, event):
        if not self._drag_item and not getattr(self, "_drag_group", None):
            return
        if not self._drag_active:
            if self._drag_origin is None:
                return
            dx = event.x - self._drag_origin[0]
            dy = event.y - self._drag_origin[1]
            if dx * dx + dy * dy < 8 * 8:
                return
            self._drag_active = True
        self._auto_scroll_drag(event.y)
        row = self.tree.identify_row(event.y)
        if not row:
            target_group = self._group_at_y(event.y)
            if target_group is not None and self._drag_item is not None:
                source_group = self.manager.group_for(self._drag_item)
                if source_group is not target_group:
                    source_group.items.remove(self._drag_item)
                    target_group.items.append(self._drag_item)
                    self._refresh_tree(); self._save_config()
            return
        target_group = next((group for group in self.manager.groups if getattr(group, "_tree_id", None) == row), None)
        target = next((item for item in self.manager.items if getattr(item, "_tree_id", None) == row), None)
        drag_group = getattr(self, "_drag_group", None)
        if drag_group is not None:
            if target_group is None or target_group is drag_group or target_group.locked and drag_group.locked:
                return
            source_index, target_index = self.manager.groups.index(drag_group), self.manager.groups.index(target_group)
            self.manager.groups.insert(target_index, self.manager.groups.pop(source_index))
        elif target is not None and target is not self._drag_item:
            source_group = self.manager.group_for(self._drag_item)
            target_group = self.manager.group_for(target)
            source_group.items.remove(self._drag_item)
            insert_at = target_group.items.index(target)
            target_box = self.tree.bbox(target._tree_id, "#0")
            if target is target_group.items[-1] and target_box and event.y > target_box[1] + target_box[3] / 2:
                insert_at = len(target_group.items)
            target_group.items.insert(insert_at, self._drag_item)
        elif target_group is not None:
            source_group = self.manager.group_for(self._drag_item)
            destination_group = target_group
            # The upper half of a group header is an explicit drop zone for
            # the previous group's tail, making the boundary usable even
            # though Treeview has no blank row between groups.
            group_box = self.tree.bbox(target_group._tree_id, "#0")
            if group_box and event.y < group_box[1] + group_box[3] / 2:
                target_index = self.manager.groups.index(target_group)
                if target_index > 0:
                    destination_group = self.manager.groups[target_index - 1]
            if source_group is not destination_group:
                source_group.items.remove(self._drag_item)
                destination_group.items.append(self._drag_item)
            else:
                return
        self._refresh_tree()
        self._save_config()

    def _drag_end(self, _event):
        self._drag_item = None
        self._drag_group = None
        self._drag_origin = None
        self._drag_active = False
        self._drag_scroll_at = 0.0

    def _group_at_y(self, y):
        visible = []
        for group in self.manager.groups:
            header = self.tree.bbox(getattr(group, "_tree_id", ""), "#0")
            if not header:
                continue
            visible.append((group, header))
            bottom = header[1] + header[3]
            if group.expanded and group.items:
                box = self.tree.bbox(getattr(group.items[-1], "_tree_id", ""), "#0")
                if box:
                    bottom = box[1] + box[3]
            if header[1] <= y <= bottom + 16:
                return group
        if visible:
            group, header = visible[-1]
            if y >= header[1] + header[3]:
                return group
        return None

    def _auto_scroll_drag(self, y):
        height = max(self.tree.winfo_height(), 1)
        now = time.monotonic()
        if now - getattr(self, "_drag_scroll_at", 0.0) < 0.12:
            return
        if y < 34:
            self.tree.yview_scroll(-1, "units")
            self._drag_scroll_at = now
        elif y > height - 34:
            self.tree.yview_scroll(1, "units")
            self._drag_scroll_at = now

    def _show_log(self, item):
        self._log_render_id += 1
        render_id = self._log_render_id
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        if not item:
            self.log_status.configure(text=self._t("no_process"))
            self._refresh_process_terminal()
            return
        lines = list(item.lines[-1200:])
        suffix = self._t("running") if item.running else self._t("stopped")
        self.log_status.configure(text=f"{self._item_name(item)}  /  {len(item.lines)} {self._t('lines')} / {suffix}")
        self._refresh_process_terminal()
        def render_chunk(offset=0):
            if render_id != self._log_render_id:
                return
            for line in lines[offset:offset + 60]:
                for text, tag in ansi_segments(line):
                    self.log.insert("end", text, tag)
                self.log.insert("end", "\n")
            next_offset = offset + 60
            if next_offset < len(lines):
                self.after_idle(lambda: render_chunk(next_offset))
            else:
                self.log.see("end")
        render_chunk()

    def _log_keypress(self, event):
        # Keep the log read-only while allowing normal mouse selection and
        # platform copy shortcuts (Cmd+C on macOS, Ctrl+C elsewhere).
        if event.keysym.lower() == "c" and (event.state & (0x4 | 0x8)):
            return
        return "break"

    def _schedule_log_refresh(self, item):
        if self._log_refresh_pending:
            return
        self._log_refresh_pending = True
        def refresh():
            self._log_refresh_pending = False
            if item is self.selected:
                self._show_log(item)
        self.after(80, refresh)

    def _start(self):
        for item in self.selected_items:
            self.manager.start(item)
        if self.selected_items:
            self._save_config()

    def _stop(self):
        for item in self.selected_items:
            self.manager.stop(item)

    def _toggle_processes(self):
        if not self.selected_items:
            return
        running_targets = [item for item in self.selected_items if item.running]
        if len(self.selected_items) > 1 and running_targets:
            label = self._t("process_list") if self.selected_groups else self._t("name")
            if not messagebox.askyesno(self._t("stop_process"), self._t("confirm_stop_all", label=label), parent=self._dialog_parent()):
                return
            self._stop()
        elif len(self.selected_items) == 1 and self.selected_items[0].running:
            if not messagebox.askyesno(self._t("stop_process"), self._t("confirm_stop_one", name=self.selected_items[0].spec.name), parent=self._dialog_parent()):
                return
            self._stop()
        else:
            self._start()

    def _restart_processes(self):
        """Restart all selected processes after a completed long press."""
        targets = list(self.selected_items)
        if not targets:
            return
        for item in targets:
            if item.running:
                self.manager.stop(item)
        self.after(180, lambda: self._restart_remaining(targets))

    def _restart_remaining(self, targets):
        for item in targets:
            if item in self.manager.items and not item.running:
                self.manager.start(item)
        self._save_config()

    def _toggle_gui(self):
        if not self.selected_items or not any(item.running for item in self.selected_items):
            return
        current = self.selected.gui_mode if self.selected else "auto"
        next_mode = {"auto": "show", "show": "hide", "hide": "auto"}[current]
        for item in self.selected_items:
            item.gui_mode = next_mode
        self._sync_gui_visibility()
        self._update_action_labels()
        self._refresh_tree()

    def _manager_event(self, kind, item, payload):
        if kind in {"changed", "exit"}:
            self.after_idle(self._refresh_tree)
        if kind == "changed" and item.running:
            visible = self._desired_gui_visibility(item)
            self.window_controller.set_visible(item, visible, focus=visible and item in self.selected_items)
            if visible and item in self.selected_items:
                self.after(250, lambda: self._focus_selected_window(item))
            if item in self.selected_items:
                self.after_idle(self._refresh_tree)
        if item is self.selected and kind == "line" and item.running:
            self._schedule_log_refresh(item)
        elif item is self.selected and kind == "exit":
            self._schedule_log_refresh(item)
        if kind == "exit":
            if item in self.selected_items:
                self.window_controller.set_visible(item, False)
            self.after_idle(self._refresh_tree)
            self.status.configure(text=self._t("exited", name=self._item_name(item), code=payload))

    def _tick(self):
        self.manager.drain_events()
        self.after(100, self._tick)

    def _close(self):
        running = [item for item in self.manager.items if item.running]
        if running and not messagebox.askyesno(
            self._t("exiting"),
            self._t("confirm_exit"),
            parent=self._dialog_parent(),
        ):
            return
        self.manager.stop_all_and_wait()
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
