import sys
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from app import (
    App,
    ManagedProcess,
    PlatformRuntime,
    ProcessManager,
    ProcessSpec,
    WindowController,
    ansi_segments,
    clean_terminal_text,
    command_with_language,
    child_environment,
    detect_platform,
    probe_language_support,
    resolve_config_path,
    select_platform_command,
    translate_command,
    user_config_path,
)
from lsl_registry import read_registry, register_process, registry_path, scan_lsl_streams


class ProcessManagerTests(unittest.TestCase):
    def test_scan_lsl_streams_finds_stream_identity_and_deduplicates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text(
                "from pylsl import StreamInfo\nStreamInfo('Demo', 'DemoType', 1, 0, 'string', 'id')\n"
                "StreamInfo('Demo', 'DemoType', 1, 0, 'string', 'id')\n", encoding="utf-8"
            )
            streams = scan_lsl_streams("cd .\npython main.py", root)
            self.assertEqual([(item["name"], item["type"]) for item in streams], [("Demo", "DemoType")])

    def test_registry_round_trip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active.json"
            register_process({"pid": 123, "name": "demo", "streams": []}, path)
            self.assertEqual(read_registry(path)[0]["pid"], 123)

    def test_registry_path_uses_windows_appdata(self):
        with patch("lsl_registry.platform.system", return_value="Windows"), patch.dict(
            "os.environ", {"APPDATA": r"C:\Users\tester\AppData\Roaming"}
        ):
            self.assertEqual(registry_path().name, "active_lsl_processes.json")
            self.assertIn("BCIControl", registry_path().parts)

    def test_scanner_follows_only_imported_publisher_class(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outlets.py").write_text(
                "from pylsl import StreamInfo\n"
                "class Sender:\n    def start(self): StreamInfo('Sent', 'Demo', 1, 0, 'string', 'id')\n"
                "class Other:\n    def start(self): StreamInfo('Other', 'Wrong', 1, 0, 'string', 'id')\n",
                encoding="utf-8",
            )
            (root / "main.py").write_text("from outlets import Sender\nSender().start()\n", encoding="utf-8")
            streams = scan_lsl_streams("python main.py", root)
            self.assertEqual([(item["name"], item["type"]) for item in streams], [("Sent", "Demo")])

    def test_scanner_selects_current_platform_command_block(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "win.py").write_text("from pylsl import StreamInfo\nStreamInfo('Win', 'Demo', 1, 0, 'string', 'id')", encoding="utf-8")
            (root / "mac.py").write_text("from pylsl import StreamInfo\nStreamInfo('Mac', 'Demo', 1, 0, 'string', 'id')", encoding="utf-8")
            command = "[windows]\ncd .\npython win.py\n[macos]\ncd .\npython mac.py"
            with patch("lsl_registry.platform.system", return_value="Windows"):
                streams = scan_lsl_streams(command, root)
            self.assertEqual(streams[0]["name"], "Win")
    def test_detect_platform_maps_supported_hosts(self):
        with patch("app.platform.system", return_value="Windows"):
            self.assertEqual(detect_platform(), "windows")
        with patch("app.platform.system", return_value="Darwin"):
            self.assertEqual(detect_platform(), "macos")
        with patch("app.platform.system", return_value="Linux"), patch(
            "app.Path.read_text", return_value='ID="ubuntu"\n'
        ):
            self.assertEqual(detect_platform(), "ubuntu")

    def test_platform_command_block_is_selected(self):
        command = "[windows]\necho win\n[macos]\necho mac\n[default]\necho fallback"
        self.assertEqual(select_platform_command(command, "windows"), ("echo win", "windows"))
        self.assertEqual(select_platform_command(command, "ubuntu"), ("echo fallback", "ubuntu"))

    def test_windows_command_translation(self):
        command = "cd MuJoCo_Sim\\bci-control-sim\nenv -u PYTHONPATH uv run python main.py \\\n+  --config /Users/apple/config.yaml"
        translated = translate_command(command, "windows")
        self.assertIn('cd /d "MuJoCo_Sim\\bci-control-sim"', translated)
        self.assertIn('set "PYTHONPATH=" && uv run python main.py', translated)
        self.assertIn("%USERPROFILE%/config.yaml", translated)

    def test_runtime_uses_native_shell_for_each_platform(self):
        self.assertEqual(PlatformRuntime("windows").command_args("echo ok")[:3], ["cmd.exe", "/d", "/s"])
        self.assertEqual(PlatformRuntime("macos").popen_kwargs(), {"start_new_session": True})
        self.assertEqual(PlatformRuntime("ubuntu").popen_kwargs(), {"start_new_session": True})

    def test_runtime_keeps_platform_specific_script_forms(self):
        with patch("app.shutil.which", return_value="/usr/bin/script"):
            self.assertEqual(
                PlatformRuntime("macos").command_args("echo ok")[:5],
                ["/usr/bin/arch", "-arm64", "script", "-q", "/dev/null"],
            )
            self.assertEqual(PlatformRuntime("ubuntu").command_args("echo ok")[:3], ["script", "-q", "-c"])

    def test_language_argument_tracks_ui_language(self):
        command = "python tool.py --value 1"
        self.assertEqual(command_with_language(command, "zh", "macos"), "python tool.py --value 1 --lang ch")
        self.assertEqual(command_with_language(command, "en", "macos"), "python tool.py --value 1 --lang en")
        self.assertEqual(command_with_language("python tool.py --lang ch", "en", "macos"), "python tool.py --lang en")

    def test_child_environment_does_not_leak_frozen_tk_paths(self):
        with patch.dict("os.environ", {"TCL_LIBRARY": "/bundle/tcl", "TK_LIBRARY": "/bundle/tk"}, clear=False):
            environment = child_environment()
        self.assertNotIn("TCL_LIBRARY", environment)
        self.assertNotIn("TK_LIBRARY", environment)

    def test_language_support_is_detected_statically_without_launching_process(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tool.py").write_text(
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--lang', choices=('ch', 'en'))\n",
                encoding="utf-8",
            )
            with patch("app.subprocess.Popen") as popen:
                self.assertTrue(probe_language_support("python tool.py", root, "macos"))
            popen.assert_not_called()

    def test_language_support_static_scan_follows_imports_and_known_frameworks(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("from cli import run\nrun()\n", encoding="utf-8")
            (root / "cli.py").write_text(
                "import click\n"
                "@click.option('--lang')\n"
                "def run(lang=None): pass\n",
                encoding="utf-8",
            )
            self.assertTrue(probe_language_support("python main.py", root, "macos"))

    def test_language_support_static_scan_accepts_virtualenv_python(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "sample"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "cli.py").write_text(
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--lang')\n",
                encoding="utf-8",
            )
            self.assertTrue(probe_language_support(".venv/bin/python -m sample.cli", root, "macos"))

    def test_language_support_static_scan_uses_package_main_module(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "sample"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "__main__.py").write_text("from .cli import main\nmain()\n", encoding="utf-8")
            (package / "cli.py").write_text(
                "import argparse\n"
                "def main():\n"
                "    parser = argparse.ArgumentParser()\n"
                "    parser.add_argument('--lang')\n",
                encoding="utf-8",
            )
            self.assertTrue(probe_language_support("python -m sample", root, "macos"))

    def test_language_support_static_scan_defaults_to_false_when_unknown(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gui.py").write_text("print('GUI startup')\n", encoding="utf-8")
            with patch("app.subprocess.Popen") as popen:
                self.assertFalse(probe_language_support("python gui.py", root, "macos"))
            popen.assert_not_called()

    def test_process_manager_passes_selected_language_to_supported_process(self):
        events = []
        command = f'{sys.executable} -c "import sys; print(sys.argv[-2:])"'
        manager = ProcessManager(Path.cwd(), lambda *args: events.append(args), lang="en")
        manager.runtime = PlatformRuntime("posix")
        item = manager.add(ProcessSpec("language-aware", command, supports_lang=True))

        manager.start(item)
        deadline = time.time() + 3
        while item.running and time.time() < deadline:
            manager.drain_events()
            time.sleep(0.02)
        manager.drain_events()

        self.assertEqual(item.return_code, 0)
        self.assertTrue(any("--lang" in line and "en" in line for line in item.lines))

    def test_user_config_path_is_outside_macos_app_bundle(self):
        self.assertEqual(
            user_config_path("macos"),
            Path.home() / "Library" / "Application Support" / "IntegratedTerm" / "processes.json",
        )

    def test_config_path_maps_saved_user_home(self):
        resolved = resolve_config_path("/Users/old-user/project", "ubuntu")
        self.assertEqual(resolved, Path.home() / "project")

    def test_terminal_controls_are_rendered(self):
        raw = "\x04\b\b\x1b[2mResolved \x1b[1m284 packages\x1b[0m\x1b[2min 17ms\x1b[0m"
        self.assertEqual(clean_terminal_text(raw), "Resolved 284 packagesin 17ms")
        segments = list(ansi_segments("\x1b[2mResolved \x1b[1m284 packages\x1b[0m"))
        self.assertEqual(segments, [("Resolved ", "dim"), ("284 packages", "bold")])

    def test_process_manager_captures_output_and_exit(self):
        events = []
        manager = ProcessManager(Path.cwd(), lambda *args: events.append(args))
        item = manager.add(ProcessSpec("echo", f'{sys.executable} -c "print(123)"'))
        manager.start(item)
        deadline = time.time() + 3
        while item.running and time.time() < deadline:
            manager.drain_events()
            time.sleep(0.02)
        manager.drain_events()
        self.assertEqual(item.return_code, 0)
        self.assertTrue(any(event[0] == "line" and "123" in (event[2] or "") for event in events))
        self.assertTrue(item.lines)
        self.assertTrue(any(event[0] == "exit" for event in events))

    def test_exit_event_does_not_wait_for_descendant_stdout(self):
        import tempfile

        events = []
        with tempfile.TemporaryDirectory() as directory:
            manager = ProcessManager(Path(directory), lambda *args: events.append((time.monotonic(), *args)))
            code = (
                "import subprocess,sys; "
                "subprocess.Popen([sys.executable,'-c','import time; time.sleep(1)']); "
                "print('parent done')"
            )
            manager.runtime.translated_command_args = lambda _command: [sys.executable, "-c", code]
            manager.runtime.popen_kwargs = lambda: {}
            item = manager.add(ProcessSpec("parent", "ignored"))
            started = time.monotonic()

            manager.start(item)
            deadline = started + 0.5
            while time.monotonic() < deadline and not any(event[1] == "exit" for event in events):
                manager.drain_events()
                time.sleep(0.01)

            exit_events = [event for event in events if event[1] == "exit"]
            self.assertTrue(exit_events)
            self.assertLess(exit_events[0][0] - started, 0.5)

    def test_stop_terminates_process(self):
        manager = ProcessManager(Path.cwd(), lambda *_args: None)
        item = manager.add(ProcessSpec("sleep", f'{sys.executable} -c "import time; time.sleep(10)"'))
        manager.start(item)
        self.assertTrue(item.running)
        manager.stop(item)
        deadline = time.time() + 3
        while item.running and time.time() < deadline:
            manager.drain_events()
            time.sleep(0.02)
        self.assertFalse(item.running)

    def test_stop_terminates_managed_child_process_group(self):
        manager = ProcessManager(Path.cwd(), lambda *_args: None)
        child_code = "import time; time.sleep(30)"
        command = f'{sys.executable} -c "import os,subprocess,sys,time; subprocess.Popen([sys.executable, \'-c\', {child_code!r}], preexec_fn=os.setsid); time.sleep(30)"'
        item = manager.add(ProcessSpec("tree", command))
        manager.start(item)
        time.sleep(0.15)
        descendants = manager._descendants(item.process.pid)
        self.assertTrue(descendants)
        manager.stop(item)
        deadline = time.time() + 4
        while item.running and time.time() < deadline:
            manager.drain_events()
            time.sleep(0.03)
        self.assertFalse(item.running)
        time.sleep(0.1)
        self.assertFalse(any(_pid_exists(pid) for pid in descendants))

    def test_stop_all_terminates_each_managed_instance(self):
        manager = ProcessManager(Path.cwd(), lambda *_args: None)
        first = manager.add(ProcessSpec("one", f'{sys.executable} -c "import time; time.sleep(30)"'))
        second = manager.add(ProcessSpec("two", f'{sys.executable} -c "import time; time.sleep(30)"'))
        manager.start(first); manager.start(second)
        manager.stop_all()
        deadline = time.time() + 4
        while (first.running or second.running) and time.time() < deadline:
            manager.drain_events(); time.sleep(0.03)
        self.assertFalse(first.running or second.running)

    def test_stop_all_waits_for_cleanup_after_launcher_has_exited(self):
        manager = ProcessManager(Path.cwd(), lambda *_args: None)
        item = manager.add(ProcessSpec("cleanup", "true"))
        item.stopping = True

        def finish_cleanup():
            time.sleep(0.15)
            item.stopping = False

        threading.Thread(target=finish_cleanup).start()
        started = time.monotonic()

        self.assertTrue(manager.stop_all_and_wait(timeout=1.0))
        self.assertGreaterEqual(time.monotonic() - started, 0.1)

    def test_app_close_waits_for_all_processes_before_destroy(self):
        class RunningItem:
            running = True
        class FakeManager:
            items = [RunningItem()]
            waited = False
            def stop_all_and_wait(self):
                self.waited = True
                return True
        app = object.__new__(App)
        app.manager = FakeManager()
        app._save_config = lambda: None
        app.destroy = lambda: setattr(app, "destroyed", True)
        with patch("app.messagebox.askyesno", return_value=True):
            app._close()
        self.assertTrue(app.manager.waited)
        self.assertTrue(app.destroyed)

    def test_group_membership_and_reassignment(self):
        manager = ProcessManager(Path.cwd(), lambda *_args: None)
        group = manager.add_group("仿真")
        item = manager.add(ProcessSpec("viewer", "echo viewer"), group)
        self.assertEqual(manager.group_for(item), group)
        self.assertEqual(manager.items, [item])
        self.assertTrue(manager.remove_group(group, remove_items=False))
        self.assertEqual(manager.group_for(item).name, "无分组")

    def test_process_and_group_support_anchor_relative_insertion(self):
        manager = ProcessManager(Path.cwd(), lambda *_args: None)
        first = manager.add(ProcessSpec("first", "echo first"))
        third = manager.add(ProcessSpec("third", "echo third"))
        second = manager.add(ProcessSpec("second", "echo second"), manager.groups[0], 1)
        self.assertEqual(manager.groups[0].items, [first, second, third])

        trailing = manager.add_group("trailing")
        inserted = manager.add_group("inserted", manager.groups.index(trailing))
        self.assertEqual([group.name for group in manager.groups], ["无分组", "inserted", "trailing"])

    def test_selection_anchor_prefers_last_focused_row(self):
        manager = ProcessManager(Path.cwd(), lambda *_args: None)
        first = manager.add(ProcessSpec("first", "echo first"))
        second = manager.add(ProcessSpec("second", "echo second"))
        first._tree_id = "first"
        second._tree_id = "second"
        manager.groups[0]._tree_id = "group"

        class FakeTree:
            def focus(self):
                return "second"

        app = object.__new__(App)
        app.manager = manager
        app.tree = FakeTree()
        app.selected = first
        app.selected_group = manager.groups[0]
        self.assertIs(app._selection_anchor(), second)

    def test_parse_groups_preserves_unique_no_group_position(self):
        app = object.__new__(App)
        raw = {
            "groups": [
                {"name": "仿真", "processes": []},
                {"name": "无分组", "processes": [{"name": "a", "command": "echo a"}]},
                {"name": "无分组", "processes": [{"name": "b", "command": "echo b"}]},
                {"name": "设备", "processes": []},
            ]
        }
        groups = app._parse_groups(raw)
        self.assertEqual([group.name for group in groups], ["仿真", "无分组", "设备"])
        ungrouped = next(group for group in groups if group.name == "无分组")
        self.assertTrue(ungrouped.locked)
        self.assertEqual([item.spec.name for item in ungrouped.items], ["a", "b"])

    def test_parse_groups_preserves_language_support_setting(self):
        app = object.__new__(App)
        groups = app._parse_groups({
            "groups": [{
                "name": "tools",
                "processes": [{
                    "name": "language-aware",
                    "command": "echo ok",
                    "supports_lang": True,
                    "lsl_streams": [{}],
                }],
            }],
        })
        self.assertTrue(groups[0].items[0].spec.supports_lang)

    def test_group_open_close_events_are_ignored_during_tree_refresh(self):
        app = object.__new__(App)
        app._refreshing_tree = True
        group = type("Group", (), {"expanded": True})()
        app.tree = type("Tree", (), {"focus": lambda self: "stale"})()
        app.manager = type("Manager", (), {"groups": [group]})()
        app._group_opened()
        app._group_closed()
        self.assertTrue(group.expanded)

    def test_drag_requires_deliberate_pointer_movement(self):
        manager = ProcessManager(Path.cwd(), lambda *_args: None)
        item = manager.add(ProcessSpec("kept", "echo kept"))
        item._tree_id = "item"

        class FakeTree:
            def identify_row(self, _y):
                return "item"

        event = type("Event", (), {"x": 10, "y": 10})()
        app = object.__new__(App)
        app.tree = FakeTree()
        app.manager = manager
        app._drag_group = None
        app._drag_item = None
        app._drag_origin = None
        app._drag_active = False
        app._auto_scroll_drag = lambda _y: self.fail("small pointer movement must not start dragging")

        app._drag_start(event)
        app._drag_motion(type("Event", (), {"x": 14, "y": 14})())

        self.assertFalse(app._drag_active)
        self.assertEqual(manager.items, [item])

    def test_stopping_state_is_visible_immediately(self):
        app = object.__new__(App)
        app.lang = "zh"
        item = ManagedProcess(ProcessSpec("demo", "echo demo"), stopping=True)
        self.assertEqual(app._item_state_text(item), "正在终止")

    def test_exited_state_text_does_not_require_status_message_placeholders(self):
        app = object.__new__(App)
        app.lang = "zh"
        item = ManagedProcess(ProcessSpec("demo", "echo demo"), return_code=1)
        self.assertEqual(app._item_state_text(item), "已退出 1")

    def test_delayed_close_event_uses_current_tree_state(self):
        group = type("Group", (), {"expanded": True, "_tree_id": "group"})()

        class FakeTree:
            def focus(self):
                return "group"

            def exists(self, iid):
                return iid == "group"

            def item(self, iid, option):
                self.last_item = (iid, option)
                return True

        app = object.__new__(App)
        app._refreshing_tree = False
        app.tree = FakeTree()
        app.manager = type("Manager", (), {"groups": [group]})()
        app.after_idle = lambda callback: callback()
        app._save_config = lambda: self.fail("unchanged expansion state must not be saved")

        app._group_closed()

        self.assertTrue(group.expanded)
        self.assertEqual(app.tree.last_item, ("group", "open"))

    def test_expansion_sync_ignores_deleted_tree_row(self):
        group = type("Group", (), {"expanded": True, "_tree_id": "new-group"})()
        tree = type(
            "Tree",
            (),
            {
                "focus": lambda self: "old-group",
                "exists": lambda self, iid: False,
            },
        )()
        app = object.__new__(App)
        app._refreshing_tree = False
        app.tree = tree
        app.manager = type("Manager", (), {"groups": [group]})()
        app.after_idle = lambda callback: callback()
        app._save_config = lambda: self.fail("stale events must not be saved")

        app._group_closed()

        self.assertTrue(group.expanded)

    def test_window_visibility_state_is_per_process_pid(self):
        controller = WindowController(None)
        item = ManagedProcess(ProcessSpec("viewer", "echo"))
        self.assertFalse(controller.is_visible(item))

    def test_window_controller_has_cross_platform_adapters(self):
        self.assertTrue(hasattr(WindowController, "_platform_window"))
        self.assertTrue(hasattr(WindowController, "_windows_window"))
        self.assertTrue(hasattr(WindowController, "_linux_window"))

    def test_window_focus_activates_only_first_window_and_switches_desktop(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        self.assertIn('if focus and visible and index == 0:', source)
        self.assertIn('[wmctrl, "-ia", window_id]', source)
        self.assertIn('[xdotool, "windowactivate", "--sync", window_id]', source)
        self.assertIn('set frontmost to true', source)


if __name__ == "__main__":
    unittest.main()


def _pid_exists(pid):
    try:
        import os
        os.kill(pid, 0)
        return True
    except OSError:
        return False
