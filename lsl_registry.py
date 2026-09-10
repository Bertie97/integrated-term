"""Cross-platform LSL publisher discovery and active-process registry."""
from __future__ import annotations

import ast
import json
import os
import platform
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_STREAM_INFO = re.compile(r"StreamInfo\s*\((?P<body>[^)]*)\)", re.DOTALL)
_QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"}
_PLATFORM_BLOCK = re.compile(r"(?ms)^\[(windows|macos|ubuntu|linux|default)\]\s*\n(.*?)(?=^\[(?:windows|macos|ubuntu|linux|default)\]\s*$|\Z)")


def registry_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "BCIControl" / "active_lsl_processes.json"


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_registry(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or registry_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return raw if isinstance(raw, list) else list(raw.get("processes", [])) if isinstance(raw, dict) else []


def write_registry(entries: list[dict[str, Any]], path: Path | None = None) -> None:
    _atomic_write(path or registry_path(), {"version": 1, "updated_at": time.time(), "processes": entries})


@contextmanager
def _registry_lock(target: Path):
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if platform.system() == "Windows":
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if platform.system() == "Windows":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def prune_registry(path: Path | None = None) -> None:
    target = path or registry_path()
    with _registry_lock(target):
        write_registry([item for item in read_registry(target) if _pid_alive(int(item.get("pid", -1)))], target)


def register_process(entry: dict[str, Any], path: Path | None = None) -> None:
    target = path or registry_path()
    with _registry_lock(target):
        entries = [item for item in read_registry(target) if _pid_alive(int(item.get("pid", -1))) and int(item.get("pid", -1)) != int(entry["pid"])]
        entries.append(entry)
        write_registry(entries, target)


def unregister_process(pid: int, path: Path | None = None, started_at: float | None = None) -> None:
    target = path or registry_path()
    def keep(item: dict[str, Any]) -> bool:
        if int(item.get("pid", -1)) != int(pid):
            return True
        return started_at is not None and float(item.get("started_at", -1.0)) != float(started_at)
    with _registry_lock(target):
        write_registry([item for item in read_registry(target) if keep(item) and _pid_alive(int(item.get("pid", -1)))], target)


def _repo_from_command(command: str, root_dir: Path) -> Path:
    match = re.search(r"(?:^|\n|;)\s*cd(?:\s+/d)?\s+([^\n;]+)", command, re.IGNORECASE)
    if not match:
        return root_dir
    raw = match.group(1).strip().strip("'\"")
    return (root_dir / raw).expanduser().resolve() if not Path(raw).is_absolute() else Path(raw).resolve()


def _current_command(command: str) -> str:
    blocks = {match.group(1): match.group(2).strip() for match in _PLATFORM_BLOCK.finditer(command)}
    if not blocks:
        return command
    system = platform.system()
    target = "windows" if system == "Windows" else "macos" if system == "Darwin" else "linux"
    return blocks.get(target, blocks.get("ubuntu" if target == "linux" else "", blocks.get("default", "")))


def _module_file(module: str, repo: Path) -> Path | None:
    relative = Path(*module.split("."))
    for base in (repo, repo / "src"):
        for candidate in (base / relative.with_suffix(".py"), base / relative / "__init__.py"):
            if candidate.is_file():
                return candidate
    return None


def _entry_file(command: str, repo: Path) -> Path | None:
    file_match = re.search(r"\b(?:python|python3|mjpython)\s+([^\s]+\.py)\b", command)
    if file_match:
        candidate = repo / file_match.group(1).strip("'\"")
        return candidate if candidate.is_file() else None
    module_match = re.search(r"\b(?:python|python3|mjpython)\s+-m\s+([A-Za-z_][\w.]*)", command)
    if module_match:
        return _module_file(module_match.group(1), repo)
    try:
        project_text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    scripts_section = project_text.partition("[project.scripts]")[2].partition("[")[0]
    for executable, module in re.findall(r'^([\w-]+)\s*=\s*["\']([\w.]+):\w+["\']', scripts_section, re.MULTILINE):
        if re.search(rf"(?<![\w-]){re.escape(executable)}(?![\w-])", command):
            return _module_file(module, repo)
    return None


def _module_name(source: Path, repo: Path) -> str:
    for base in (repo / "src", repo):
        try:
            relative = source.relative_to(base).with_suffix("")
        except ValueError:
            continue
        parts = list(relative.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)
    return source.stem


def _reachable_python(entry: Path, repo: Path) -> dict[Path, set[str] | None]:
    found: dict[Path, set[str] | None] = {}
    pending: list[tuple[Path, set[str] | None]] = [(entry, None)]
    while pending:
        source, selected = pending.pop()
        previous = found.get(source, set())
        if source in found and (previous is None or selected is not None and selected.issubset(previous)):
            continue
        found[source] = None if selected is None else set(selected) | (previous or set())
        try:
            tree = ast.parse(source.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        current = _module_name(source, repo)
        package = current if source.name == "__init__.py" else current.rpartition(".")[0]
        modules: list[tuple[str, set[str] | None]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend((alias.name, None) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    prefix = package.split(".") if package else []
                    prefix = prefix[: max(0, len(prefix) - node.level + 1)]
                    base = ".".join([*prefix, *(node.module or "").split(".")]).strip(".")
                else:
                    base = node.module or ""
                if base:
                    modules.append((base, {alias.name for alias in node.names if alias.name != "*"} or None))
                    modules.extend((f"{base}.{alias.name}", None) for alias in node.names)
        for module, symbols in modules:
            candidate = _module_file(module, repo)
            if candidate is not None:
                pending.append((candidate, symbols))
    return found


def scan_lsl_streams(command: str, root_dir: Path) -> list[dict[str, Any]]:
    command = _current_command(command).replace("\\\n", " ")
    repo = _repo_from_command(command, root_dir)
    if not repo.is_dir():
        return []
    entry = _entry_file(command, repo)
    if entry is None:
        return []
    sources = _reachable_python(entry, repo) if entry.suffix.lower() in {".py", ".pyi"} else {entry: None}
    streams: dict[tuple[str, str], dict[str, Any]] = {}

    # Some task protocols keep the stream name in a JSON task profile while
    # the outlet is constructed dynamically. Record that contract as static
    # evidence when the command names the task.
    task_match = re.search(r"--task\s+([A-Za-z_][\w-]*)", command)
    task_match = task_match or re.search(r"\brun\s+(quadrotor_hover|quadrotor_uwb|arm_reach)\b", command)
    if task_match and "keyboard_lsl" in command:
        task = task_match.group(1)
        if task in {"quadrotor_hover", "quadrotor_uwb"}:
            streams[("BCI Quadrotor Control", "BCIControlSignal")] = {
                "name": "BCI Quadrotor Control", "type": "BCIControlSignal", "evidence": [{"file": "task protocol", "line": 0}]
            }
        elif task == "arm_reach":
            streams[("BCI MiniCobo Control", "BCIControlSignal")] = {
                "name": "BCI MiniCobo Control", "type": "BCIControlSignal", "evidence": [{"file": "task protocol", "line": 0}]
            }
    if "datalink-frontend" in command or "datalink_frontend" in command:
        streams[("BCI Quadrotor Control", "BCIControlSignal")] = {
            "name": "BCI Quadrotor Control", "type": "BCIControlSignal", "evidence": [{"file": "target contract", "line": 0}
        ]}
    for source, selected_symbols in sources.items():
        if not source.is_file() or source.suffix.lower() not in {".py", ".pyi", ".cpp", ".cc", ".c", ".h", ".hpp"}:
            continue
        if any(part in _SKIP_DIRS or part in {"test", "tests"} for part in source.parts):
            continue
        try:
            text = source.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        calls: list[tuple[str, str, int]] = []
        if source.suffix.lower() in {".py", ".pyi"}:
            try:
                tree = ast.parse(text)
                constants = {node.targets[0].id: node.value.value for node in ast.walk(tree)
                             if isinstance(node, ast.Assign) and len(node.targets) == 1
                             and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)
                             and isinstance(node.value.value, str)}
                field_defaults: dict[str, str] = {}
                for node in ast.walk(tree):
                    if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                        continue
                    value = node.value.value if isinstance(node.value, ast.Constant) else constants.get(node.value.id) if isinstance(node.value, ast.Name) else None
                    if isinstance(value, str):
                        field_defaults[node.target.id] = value
                roots = [node for node in tree.body if selected_symbols is None or getattr(node, "name", None) in selected_symbols]
                for node in (child for root in roots for child in ast.walk(root)):
                    if not isinstance(node, ast.Call) or not ((isinstance(node.func, ast.Name) and node.func.id == "StreamInfo") or (isinstance(node.func, ast.Attribute) and node.func.attr == "StreamInfo")):
                        continue
                    values = list(node.args)
                    by_name = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
                    first = by_name.get("name", values[0] if values else None)
                    second = by_name.get("type", values[1] if len(values) > 1 else None)
                    def resolve(
                        value: ast.expr | None,
                        constants: dict[str, str] = constants,
                        field_defaults: dict[str, str] = field_defaults,
                    ) -> Any:
                        if isinstance(value, ast.Constant):
                            return value.value
                        if isinstance(value, ast.Name):
                            return constants.get(value.id)
                        if isinstance(value, ast.Attribute):
                            return field_defaults.get(value.attr)
                        return None

                    name, stream_type = resolve(first), resolve(second)
                    if isinstance(name, str) and isinstance(stream_type, str):
                        calls.append((name, stream_type, node.lineno))
            except SyntaxError:
                pass
        else:
            for match in _STREAM_INFO.finditer(text):
                quoted = _QUOTED.findall(match.group("body"))
                if len(quoted) >= 2:
                    calls.append((quoted[0], quoted[1], text.count("\n", 0, match.start()) + 1))
        for name, stream_type, line in calls:
            key = (name, stream_type)
            record = streams.setdefault(key, {"name": name, "type": stream_type, "evidence": []})
            evidence = {"file": source.relative_to(repo).as_posix(), "line": line}
            if evidence not in record["evidence"]:
                record["evidence"].append(evidence)
    if task_match and task_match.group(1) == "arm_reach" and "keyboard_lsl" not in command:
        streams = {key: value for key, value in streams.items() if "Quadrotor" not in key[0]}
    return sorted(streams.values(), key=lambda item: (item["type"], item["name"]))


def build_entry(pid: int, name: str, command: str, root_dir: Path, streams: list[dict[str, Any]], started_at: float | None = None) -> dict[str, Any]:
    return {"pid": int(pid), "name": name, "command": command, "repo_root": str(_repo_from_command(command, root_dir)), "streams": streams, "started_at": time.time() if started_at is None else started_at, "hostname": platform.node()}
