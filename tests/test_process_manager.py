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
    detect_platform,
    resolve_config_path,
    select_platform_command,
    translate_command,
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

    def test_group_open_close_events_are_ignored_during_tree_refresh(self):
        app = object.__new__(App)
        app._refreshing_tree = True
        group = type("Group", (), {"expanded": True})()
        app.tree = type("Tree", (), {"focus": lambda self: "stale"})()
        app.manager = type("Manager", (), {"groups": [group]})()
        app._group_opened()
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
