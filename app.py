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
from tkinter import filedialog, messagebox, simpledialog, ttk

from lsl_registry import (
    build_entry,
    prune_registry,
    register_process,
    scan_lsl_streams,
    unregister_process,
)


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "processes.json"
FA_ICON_DIR = APP_DIR / "assets" / "icons" / "fa"
ANSI_CSI = re.compile(r"\x1b\[([0-9;]*)m")
PLATFORM_BLOCK = re.compile(r"(?ms)^\[(windows|macos|ubuntu|linux|default)\]\s*\n(.*?)(?=^\[(?:windows|macos|ubuntu|linux|default)\]\s*$|\Z)")


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


class PlatformRuntime:
    """System API and shell adapter used by the process manager."""

    def __init__(self, target: str | None = None):
        self.target = target or detect_platform()

    @property
    def is_windows(self) -> bool:
        return self.target == "windows"

    def command_args(self, command: str) -> list[str]:
        command = translate_command(command, self.target)
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
        super().__init__(parent, width=width, height=height, bg="#ffffff", highlightthickness=0, bd=0)
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
        icon_width = 14 if self._icon else 0
        text_width = len(display_text) * 5.5
        content_width = text_width + (icon_width + 5 if self._icon else 0)
        content_left = (self._width - content_width) / 2
        if self._icon:
            if self._icon == "pin" and not self._text:
                icon_offset = 2
            elif display_text:
                icon_offset = -6 if display_text == "设置根目录" else -3
                if len(display_text) == 4:
                    icon_offset = -8
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
            image = self._icon_images.setdefault(cache_key, tk.PhotoImage(file=str(path)))
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


@dataclass
class ManagedProcess:
    spec: ProcessSpec
    process: subprocess.Popen | None = None
    lines: list[str] = field(default_factory=list)
    started_at: float | None = None
    return_code: int | None = None
    reader: threading.Thread | None = None
    stopping: bool = False
    gui_mode: str = "auto"  # auto: selected process is visible; show/hide are persistent

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None


@dataclass
class ProcessGroup:
    name: str
    items: list[ManagedProcess] = field(default_factory=list)
    locked: bool = False
    expanded: bool = True


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
    def __init__(self, root_dir: Path, on_event):
        prune_registry()
        self.root_dir = root_dir
        self.runtime = PlatformRuntime()
        self.on_event = on_event
        self.groups: list[ProcessGroup] = [ProcessGroup("无分组", locked=True)]
        self.events: queue.Queue[tuple[str, ManagedProcess, str | None]] = queue.Queue()

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
        command = translate_command(item.spec.command, self.runtime.target)
        try:
            item.process = subprocess.Popen(
                self.runtime.command_args(item.spec.command),
                cwd=str(self.root_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
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
        register_process(build_entry(item.process.pid, item.spec.name, command, self.root_dir, item.spec.lsl_streams, item.started_at))
        item.reader = threading.Thread(target=self._read_output, args=(item,), daemon=True)
        item.reader.start()
        self.on_event("changed", item, None)

    def _read_output(self, item: ManagedProcess) -> None:
        assert item.process is not None
        stream = item.process.stdout
        if stream:
            for line in iter(stream.readline, ""):
                self.events.put(("line", item, line.rstrip("\r\n")))
            stream.close()
        item.process.wait()
        item.return_code = item.process.returncode
        self.events.put(("exit", item, str(item.return_code)))
        unregister_process(item.process.pid, started_at=item.started_at)

    def stop(self, item: ManagedProcess) -> None:
        if not item.running or item.process is None or item.stopping:
            return
        item.stopping = True
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
            return
        try:
            pgid = os.getpgid(pid)
        except OSError:
            item.stopping = False
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

    def drain_events(self) -> None:
        while True:
            try:
                kind, item, payload = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "line" and payload is not None:
                item.lines.append(payload)
                item.lines[:] = item.lines[-4000:]
            self.on_event(kind, item, payload)


def shutil_available(name: str) -> bool:
    from shutil import which
    return which(name) is not None


class ProcessDialog(tk.Toplevel):
    def __init__(self, parent, initial: ProcessSpec | None = None, readonly: bool = False):
        super().__init__(parent)
        self._escape_tag = f"ProcessDialogEscape{id(self)}"
        self.bind_class(self._escape_tag, "<KeyPress-Escape>", self._cancel)
        self.title("添加进程" if initial is None else ("进程配置" if readonly else "编辑进程"))
        self.readonly = readonly
        self.resizable(False, False)
        self.result: ProcessSpec | None = None
        self.transient(parent)
        if isinstance(parent, tk.Toplevel):
            self.attributes("-topmost", True)
        self.grab_set()
        self.after_idle(self._present)
        body = ttk.Frame(self, padding=18)
        body.grid(sticky="nsew")
        ttk.Label(body, text="进程名称").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.name_var = tk.StringVar(value=initial.name if initial else "")
        self.name_entry = ttk.Entry(body, textvariable=self.name_var, width=42, state="readonly" if readonly else "normal")
        self.name_entry.grid(row=1, column=0, pady=(0, 12))
        ttk.Label(body, text="CLI 命令").grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.command_text = tk.Text(body, width=48, height=6, wrap="none", bg="#f8fafc", fg="#243954", relief="solid", borderwidth=1, font=("SFMono-Regular", 10))
        self.command_text.grid(row=3, column=0, pady=(0, 14))
        if initial:
            self.command_text.insert("1.0", initial.command)
        if readonly:
            self.command_text.configure(state="disabled")
        ttk.Label(body, text="使用⇪+↩︎换行", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(0, 8))
        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, sticky="e")
        cancel_button = ttk.Button(actions, text="关闭" if readonly else "取消", command=self.destroy)
        cancel_button.pack(side="right", padx=(8, 0))
        focus_widgets = [body, self.name_entry, self.command_text, actions, cancel_button]
        if not readonly:
            save_button = ttk.Button(actions, text="保存", command=self.save)
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
            messagebox.showwarning("信息不完整", "请填写进程名称和 CLI 命令。", parent=self)
            return
        self.result = ProcessSpec(name, command)
        self.destroy()


class GroupRemoveDialog(tk.Toplevel):
    def __init__(self, parent, group_count: int):
        super().__init__(parent)
        self.result = "cancel"
        self.title("移除分组")
        self.transient(parent)
        if isinstance(parent, tk.Toplevel):
            self.attributes("-topmost", True)
        self.grab_set(); self.resizable(False, False)
        body = ttk.Frame(self, padding=20); body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"将移除选中的 {group_count} 个分组。是否同时移除组内进程？").pack(anchor="w", pady=(0, 14))
        buttons = ttk.Frame(body); buttons.pack(anchor="e")
        def choose(value):
            self.result = value; self.destroy()
        ttk.Button(buttons, text="释放", command=lambda: choose("release")).pack(side="left")
        ttk.Button(buttons, text="否", command=lambda: choose("cancel")).pack(side="left", padx=8)
        ttk.Button(buttons, text="是", command=lambda: choose("delete")).pack(side="left")
        self.bind("<Escape>", lambda _event: choose("cancel"))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("集成终端控制台")
        self.geometry("1180x720")
        self.minsize(900, 560)
        self.configure(bg="#f4f7fb")
        self._style()
        self.runtime = PlatformRuntime()
        self.root_dir = resolve_config_path(self._load_config().get("root_dir", str(APP_DIR)), self.runtime.target)
        self.manager = ProcessManager(self.root_dir, self._manager_event)
        self.window_controller = WindowController(self)
        self.selected: ManagedProcess | None = None
        self.selected_items: list[ManagedProcess] = []
        self.selected_group: ProcessGroup | None = None
        self.selected_groups: list[ProcessGroup] = []
        self._drag_item: ManagedProcess | None = None
        self._refreshing_tree = False
        self._log_render_id = 0
        self._log_refresh_pending = False
        self.process_list_window: tk.Toplevel | None = None
        self.process_list_tree: ttk.Treeview | None = None
        self.floating_start_button: RoundedButton | None = None
        self.floating_gui_button: RoundedButton | None = None
        self.floating_edit_button: RoundedButton | None = None
        self.process_terminal_window: tk.Toplevel | None = None
        self.process_terminal_text: tk.Text | None = None
        self.process_terminal_status: ttk.Label | None = None
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

    def _build_ui(self):
        header = ttk.Frame(self, padding=(24, 18, 24, 12))
        header.pack(fill="x")
        ttk.Label(header, text="集成终端", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  控制台", style="Muted.TLabel").pack(side="left", pady=(4, 0))
        ttk.Label(header, text=f"  {self.runtime.target}", style="Muted.TLabel").pack(side="left", pady=(4, 0))
        RoundedButton(header, text="设置根目录", command=self._choose_root, width=112, icon="folder").pack(side="right")
        RoundedButton(header, text="导入配置", command=self._import_config, width=98, icon="import").pack(side="right", padx=(0, 8))
        RoundedButton(header, text="保存配置", command=self._export_config, width=98, icon="save").pack(side="right", padx=(0, 8))
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
        top = ttk.Frame(left_content, style="Panel.TFrame")
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(top, text="进程列表", style="SectionTitle.TLabel").pack(side="left")
        RoundedButton(top, text="", command=self._toggle_process_list_window, width=38, icon="pin").pack(side="right", padx=(6, 0))
        RoundedButton(top, text="添加进程", command=self._add, width=108, icon="plus").pack(side="right", padx=(6, 0))
        RoundedButton(top, text="新建分组", command=self._add_group, width=108, icon="plus").pack(side="right")
        self.tree = ttk.Treeview(left_content, columns=("gui", "state", "pid"), show="tree headings", selectmode="extended")
        self._style_tree_arrows()
        self.tree.tag_configure("running", foreground="#18864b", background="#effaf3")
        self.tree.tag_configure("idle", foreground="#243954")
        self.tree.tag_configure("group", foreground="#1d5fa7", background="#eef5fc", font=("SF Pro Display", 12, "bold"))
        self.tree.heading("#0", text="名称", anchor="w")
        self.tree.heading("gui", text="界面", anchor="w")
        self.tree.heading("state", text="状态", anchor="w")
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
        self.start_button = RoundedButton(actions, text="启动", command=self._toggle_processes, width=112, accent=True, icon="play", long_press_command=self._restart_processes)
        self.start_button.pack(side="left")
        self.gui_button = RoundedButton(actions, text="自动调度", command=self._toggle_gui, width=112, icon="monitor")
        self.gui_button.pack(side="left", padx=6)
        self.edit_button = RoundedButton(actions, text="编辑", command=self._edit, width=82, icon="edit")
        self.edit_button.pack(side="right")
        self.remove_button = RoundedButton(actions, text="移除", command=self._remove, width=82, icon="trash")
        self.remove_button.pack(side="right", padx=6)

        log_header = ttk.Frame(right_content, style="Panel.TFrame")
        log_header.pack(fill="x", pady=(0, 10))
        ttk.Label(log_header, text="终端输出", style="SectionTitle.TLabel").pack(side="left")
        self.log_status = ttk.Label(log_header, text="未选择进程", style="Panel.TLabel")
        self.log_status.pack(side="right")
        self.log = tk.Text(right_content, bg="#f8fafc", fg="#263a54", insertbackground="#0b72e7", relief="flat", borderwidth=0, highlightthickness=0, wrap="none", font=("SFMono-Regular", 10), padx=14, pady=14)
        self.log.tag_configure("bold", font=("SFMono-Regular", 10, "bold"))
        self.log.tag_configure("dim", foreground="#7d8da3")
        ansi_colors = {"color0": "#263a54", "color1": "#c0392b", "color2": "#20854a", "color3": "#a66a00", "color4": "#1769aa", "color5": "#8e44ad", "color6": "#087f8c", "color7": "#53657a"}
        for tag, color in ansi_colors.items():
            self.log.tag_configure(tag, foreground=color)
        self.log.pack(fill="both", expand=True, padx=4, pady=4)
        self.log.bind("<KeyPress>", self._log_keypress)
        self.status = ttk.Label(self, text="就绪", style="Muted.TLabel", padding=(24, 0, 24, 10))
        self.status.pack(fill="x")

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
            style.layout("Pretty.Treeview.Heading", [("Treeheading.text", {"sticky": "w"}), ("Treeheading.image", {"side": "right", "sticky": ""})])
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
        window.title("集成终端 - 进程列表")
        window.geometry("560x520")
        window.minsize(420, 280)
        window.attributes("-topmost", True)
        window.configure(bg="#f4f7fb")
        panel = RoundedPanel(window, padding=10)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        frame = panel.content
        tree = ttk.Treeview(frame, columns=("gui", "state", "pid"), show="tree headings", selectmode="extended")
        tree.configure(style="Pretty.Treeview")
        tree.heading("#0", text="名称", anchor="w")
        tree.heading("gui", text="界面", anchor="w")
        tree.heading("state", text="状态", anchor="w")
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
        self.floating_start_button = RoundedButton(actions, text="启动", command=self._toggle_processes, width=88, accent=True, icon="play", long_press_command=self._restart_processes)
        self.floating_start_button.pack(side="left")
        self.floating_gui_button = RoundedButton(actions, text="自动调度", command=self._toggle_gui, width=88, icon="monitor")
        self.floating_gui_button.pack(side="left", padx=6)
        RoundedButton(actions, text="终端", command=self._toggle_process_terminal_window, width=78, icon="terminal").pack(side="left")
        self.floating_edit_button = RoundedButton(actions, text="编辑", command=self._edit, width=78, icon="edit")
        self.floating_edit_button.pack(side="right")
        RoundedButton(actions, text="移除", command=self._remove, width=78, icon="trash").pack(side="right", padx=6)
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
        window.title("集成终端 - 终端输出")
        window.geometry("720x440")
        window.minsize(460, 260)
        window.attributes("-topmost", True)
        window.configure(bg="#f4f7fb")
        panel = RoundedPanel(window, padding=10)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        self.process_terminal_status = ttk.Label(panel.content, text="未选择进程", style="SectionTitle.TLabel")
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
            status.configure(text="未选择进程")
        else:
            suffix = "运行中" if self.selected.running else "已停止"
            status.configure(text=f"{self.selected.spec.name}  /  {len(self.selected.lines)} 行 / {suffix}")
            output.insert("end", "\n".join(clean_terminal_text(line) for line in self.selected.lines[-1200:]))
            output.see("end")
        # Keep the pinned terminal readable from the beginning of long lines.
        output.xview_moveto(0)
        output.configure(state="disabled")

    def _refresh_process_list_window(self):
        tree = self.process_list_tree
        if tree is None or not tree.winfo_exists():
            return
        tree.delete(*tree.get_children())
        self._floating_tree_map.clear()
        for group in self.manager.groups:
            running = any(item.running for item in group.items)
            marker = "▾" if group.expanded else "▸"
            group_iid = tree.insert("", "end", text=f"    {marker}  {group.name}", values=("", "运行中" if running else "空闲", ""), open=group.expanded, tags=("running" if running else "idle", "group"))
            self._floating_tree_map[group_iid] = group
            if group.expanded:
                for item in group.items:
                    state = "运行中" if item.running else (f"已退出 {item.return_code}" if item.return_code is not None else "空闲")
                    pid = str(item.process.pid) if item.process and item.running else "-"
                    iid = tree.insert(group_iid, "end", text=item.spec.name, values=({"auto": "◩", "show": "▣", "hide": "▢"}[item.gui_mode], state, pid), tags=("running" if item.running else "idle",))
                    self._floating_tree_map[iid] = item

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

    def _group_opened(self, _event=None):
        self._set_group_expanded(True)

    def _group_closed(self, _event=None):
        self._set_group_expanded(False)

    def _set_group_expanded(self, expanded: bool):
        iid = self.tree.focus()
        group = next((group for group in self.manager.groups if getattr(group, "_tree_id", None) == iid), None)
        if group:
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
            "groups": [{"name": group.name, "locked": group.locked, "expanded": group.expanded,
                        "processes": [{"name": item.spec.name, "command": item.spec.command, "enabled": item.spec.enabled,
                                       "gui_mode": item.gui_mode, "lsl_streams": item.spec.lsl_streams} for item in group.items]}
                       for group in self.manager.groups],
        }

    def _load_config(self):
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_config(self):
        CONFIG_PATH.write_text(json.dumps(self._config_data(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _export_config(self):
        target = filedialog.asksaveasfilename(
            title="保存配置文件",
            initialdir=str(self.root_dir),
            initialfile="bci-device-config.json",
            defaultextension=".json",
            filetypes=[("JSON 配置", "*.json"), ("所有文件", "*")],
        )
        if not target:
            return
        try:
            Path(target).write_text(json.dumps(self._config_data(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法保存配置文件：\n{exc}", parent=self)
            return
        self.status.configure(text=f"配置已保存：{target}")

    def _import_config(self):
        source = filedialog.askopenfilename(
            title="导入配置文件",
            initialdir=str(self.root_dir),
            filetypes=[("JSON 配置", "*.json"), ("所有文件", "*")],
        )
        if not source:
            return
        try:
            raw = json.loads(Path(source).read_text(encoding="utf-8"))
            root_dir = resolve_config_path(raw.get("root_dir", str(self.root_dir)), self.runtime.target)
            groups = self._parse_groups(raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            messagebox.showerror("导入失败", f"配置文件格式无效：\n{exc}", parent=self)
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
        self.status.configure(text=f"已导入 {len(self.manager.items)} 个进程、{len(self.manager.groups)} 个分组")

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
            group = ProcessGroup(name, locked=locked, expanded=bool(group_raw.get("expanded", True)))
            for entry in group_raw.get("processes", []):
                if not isinstance(entry, dict) or not str(entry.get("name", "")).strip() or not str(entry.get("command", "")).strip():
                    raise ValueError("每个进程必须包含非空的 name 和 command")
                gui_mode = str(entry.get("gui_mode", "auto")).strip().lower()
                if gui_mode not in {"auto", "show", "hide"}:
                    gui_mode = "auto"
                streams = list(entry.get("lsl_streams", [])) if isinstance(entry.get("lsl_streams", []), list) else []
                if not streams:
                    streams = scan_lsl_streams(str(entry["command"]), self.__dict__.get("root_dir", Path.cwd()))
                group.items.append(ManagedProcess(
                    ProcessSpec(str(entry["name"]).strip(), str(entry["command"]).strip(), bool(entry.get("enabled", True)), streams),
                    gui_mode=gui_mode,
                ))
            if group.name == "无分组":
                existing = next((candidate for candidate in groups if candidate.name == "无分组"), None)
                if existing is None:
                    groups.append(group)
                else:
                    existing.items.extend(group.items)
                    existing.expanded = existing.expanded or group.expanded
            else:
                groups.append(group)
        if not any(group.name == "无分组" for group in groups):
            groups.append(ProcessGroup("无分组", locked=True))
        return groups

    def _command_quit(self, _event=None):
        self._close()
        return "break"

    def _load_specs(self):
        self.manager.groups = self._parse_groups(self._load_config())
        self._save_config()
        self._refresh_tree()

    def _refresh_tree(self):
        selected_items = list(self.selected_items)
        selected_group = self.selected_group
        selected_groups = list(self.selected_groups)
        self._refreshing_tree = True
        try:
            self.tree.delete(*self.tree.get_children())
            for group in self.manager.groups:
                running = any(item.running for item in group.items)
                marker = "▾" if group.expanded else "▸"
                group_iid = self.tree.insert("", "end", text=f"    {marker}  {group.name}", values=("", "运行中" if running else "空闲", ""), open=group.expanded, tags=("running" if running else "idle", "group"))
                group._tree_id = group_iid
                if group.expanded:
                    for item in group.items:
                        state = "运行中" if item.running else (f"已退出 {item.return_code}" if item.return_code is not None else "空闲")
                        pid = str(item.process.pid) if item.process and item.running else "-"
                        gui_icon = {"auto": "◩", "show": "▣", "hide": "▢"}[item.gui_mode]
                        iid = self.tree.insert(group_iid, "end", text=item.spec.name, values=(gui_icon, state, pid), tags=("running" if item.running else "idle",))
                        item._tree_id = iid
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
        selected = filedialog.askdirectory(initialdir=str(self.root_dir), title="选择进程根目录")
        if selected:
            self.root_dir = Path(selected)
            self.manager.root_dir = self.root_dir
            self.root_label.configure(text=str(self.root_dir))
            self._save_config()
            self.status.configure(text=f"根目录：{self.root_dir}")

    def _add(self):
        dialog = ProcessDialog(self._dialog_parent())
        self.wait_window(dialog)
        if dialog.result:
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
        dialog = ProcessDialog(self._dialog_parent(), self.selected.spec, readonly=readonly)
        self.wait_window(dialog)
        if not readonly and dialog.result:
            dialog.result.lsl_streams = scan_lsl_streams(dialog.result.command, self.root_dir)
            self.selected.spec = dialog.result
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
                messagebox.showwarning("无法移除分组", "请先终止分组内所有进程。", parent=self._dialog_parent())
                return
            dialog = GroupRemoveDialog(self._dialog_parent(), len(groups)); self.wait_window(dialog)
            if dialog.result == "cancel":
                return
            for group in groups:
                self.manager.remove_group(group, remove_items=dialog.result == "delete")
            self.selected_group = None; self.selected_groups = []; self.selected_items = []
            self._save_config(); self._refresh_tree(); self._show_log(None)
            return
        removable = [item for item in self.selected_items if not item.running]
        if not removable or not messagebox.askyesno("移除进程", f"确定移除选中的 {len(removable)} 个进程吗？", parent=self._dialog_parent()):
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
        editor.insert(0, group.name)
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
                group.name = name
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
            start_text = "终止分组" if any_running else "启动分组"
            start_icon = "stop" if any_running else "play"
        elif len(self.selected_items) > 1:
            any_running = any(item.running for item in self.selected_items)
            start_text = "全部终止" if any_running else "全部启动"
            start_icon = "stop" if any_running else "play"
        elif self.selected:
            start_text = "终止" if self.selected.running else "启动"
            start_icon = "stop" if self.selected.running else "play"
        else:
            start_text, start_icon = "启动", "play"
        restart_text = "全部重启" if self.selected_groups or len(self.selected_items) > 1 else "重启"
        self.start_button.configure(text=start_text, icon=start_icon, long_press_text=restart_text)
        if self.floating_start_button is not None and self.floating_start_button.winfo_exists():
            self.floating_start_button.configure(text=start_text, icon=start_icon, long_press_text=restart_text)
        # A stopped process has no controllable windows; keep the control in a
        # predictable disabled "show" state until it is running again.
        can_control_gui = bool(self.selected_items and any(item.running for item in self.selected_items))
        selected_modes = {item.gui_mode for item in self.selected_items}
        gui_mode = self.selected.gui_mode if self.selected and len(selected_modes) != 1 else (next(iter(selected_modes)) if len(selected_modes) == 1 else "auto")
        labels = {"auto": ("自动调度", "monitor"), "show": ("保持显示", "monitor"), "hide": ("保持隐藏", "monitor")}
        gui_text, gui_icon = labels[gui_mode]
        self.gui_button.configure(text=gui_text, icon=gui_icon)
        if self.floating_gui_button is not None and self.floating_gui_button.winfo_exists():
            self.floating_gui_button.configure(text=gui_text, icon=gui_icon)
        self.gui_button.configure(state="normal" if can_control_gui else "disabled")
        if self.floating_gui_button is not None and self.floating_gui_button.winfo_exists():
            self.floating_gui_button.configure(state="normal" if can_control_gui else "disabled")
        can_edit = not self.selected_groups and len(self.selected_items) == 1
        self.edit_button.configure(text="配置" if can_edit and self.selected_items[0].running else "编辑", icon="monitor" if can_edit and self.selected_items[0].running else "edit")
        self.edit_button.configure(state="normal" if can_edit else "disabled")
        if self.floating_edit_button is not None and self.floating_edit_button.winfo_exists():
            floating_edit_text = "配置" if can_edit and self.selected_items[0].running else "编辑"
            floating_edit_icon = "monitor" if can_edit and self.selected_items[0].running else "edit"
            self.floating_edit_button.configure(text=floating_edit_text, icon=floating_edit_icon, state="normal" if can_edit else "disabled")
        can_remove = (bool(self.selected_groups) and any(not group.locked for group in self.selected_groups)) or bool(self.selected_items)
        self.remove_button.configure(state="normal" if can_remove else "disabled")

    def _drag_start(self, event):
        row = self.tree.identify_row(event.y)
        self._drag_group = next((group for group in self.manager.groups if getattr(group, "_tree_id", None) == row), None)
        self._drag_item = next((item for item in self.manager.items if getattr(item, "_tree_id", None) == row), None)

    def _drag_motion(self, event):
        if not self._drag_item and not getattr(self, "_drag_group", None):
            return
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
            self.log_status.configure(text="未选择进程")
            self._refresh_process_terminal()
            return
        lines = list(item.lines[-1200:])
        suffix = "运行中" if item.running else "已停止"
        self.log_status.configure(text=f"{item.spec.name}  /  {len(item.lines)} 行 / {suffix}")
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
            label = "分组" if self.selected_groups else "选中的进程"
            if not messagebox.askyesno("终止进程", f"确定终止{label}内的全部进程吗？", parent=self._dialog_parent()):
                return
            self._stop()
        elif len(self.selected_items) == 1 and self.selected_items[0].running:
            if not messagebox.askyesno("终止进程", f"确定终止进程“{self.selected_items[0].spec.name}”吗？", parent=self._dialog_parent()):
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
            self.window_controller.set_visible(item, self._desired_gui_visibility(item))
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
            self.status.configure(text=f"{item.spec.name} 已退出，返回码：{payload}")

    def _tick(self):
        self.manager.drain_events()
        self.after(100, self._tick)

    def _close(self):
        running = [item for item in self.manager.items if item.running]
        if running and not messagebox.askyesno(
            "退出控制台",
            "关闭控制台将同步终止所有控制台唤起的进程，是否继续？",
            parent=self._dialog_parent(),
        ):
            return
        self.manager.stop_all_and_wait()
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
