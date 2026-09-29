import ast
import importlib.util
import pathlib
import sys
import time
import tkinter as tk
import unittest


APP = pathlib.Path(__file__).parents[1] / "app.py"
PROJECT_DIR = APP.parent


class UiContractTests(unittest.TestCase):
    def setUp(self):
        self.source = APP.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_console_title_and_rounded_controls_are_declared(self):
        self.assertIn('self.title("集成终端控制台")', self.source)
        self.assertIn('RoundedButton(top, text="", command=self._toggle_process_list_window', self.source)
        self.assertIn('RoundedButton(top, text="新建分组"', self.source)
        self.assertIn('RoundedButton(top, text="添加进程"', self.source)
        self.assertIn('icon="plus"', self.source)
        self.assertIn('icon="pin"', self.source)
        self.assertIn("FA_ICON_DIR", self.source)
        self.assertIn("pre-rendered Font Awesome glyph", self.source)
        self.assertIn("class RoundedPanel", self.source)

    def test_process_language_switch_toggle_is_visible_in_configuration_dialog(self):
        self.assertIn('"supports_lang": "适配中英文切换（自动添加 --lang ch/en）"', self.source)
        self.assertIn("class LanguageToggle(tk.Canvas)", self.source)
        self.assertIn("self.supports_lang_toggle = LanguageToggle", self.source)
        self.assertNotIn("self.supports_lang_check = ttk.Checkbutton", self.source)
        self.assertIn('language_row = tk.Frame(body, bg="#f4f7fb")', self.source)
        self.assertIn('self.supports_lang_toggle.pack(side="left", padx=(0, 10))', self.source)
        self.assertNotIn("_detect_language_support", self.source)
        self.assertIn('self.tree.heading("gui", text=self._t("gui"), anchor="center")', self.source)

    def test_workflow_button_and_editor_are_declared(self):
        self.assertIn('"add_workflow": "添加工作流"', self.source)
        self.assertIn('command=self._add_workflow', self.source)
        self.assertIn("class WorkflowDialog(tk.Toplevel)", self.source)
        self.assertIn('self.available_items.bind("<Double-1>", self._add_item)', self.source)
        self.assertIn('"💤 等待一下" if lang == "zh" else "💤 Sleep for a While"', self.source)
        self.assertIn('self.items.insert("end", "[sleep](1s)")', self.source)
        self.assertIn('self.items.bind("<Double-1>", self._edit_reference_arguments)', self.source)
        self.assertIn('text=f"[{title}]("', self.source)
        self.assertIn('text=")"', self.source)
        self.assertIn('relief="flat", bd=0, highlightthickness=0', self.source)
        self.assertIn('argument_var.trace_add("write", resize)', self.source)
        self.assertIn('"plain_text_edit": "以纯文本编辑"', self.source)
        self.assertIn('"workflow_edit": "以工作流编辑"', self.source)
        self.assertIn('command=self._toggle_plain_text_edit', self.source)
        self.assertIn('self.items.grid_remove()', self.source)
        self.assertIn('self.plain_text = tk.Text(', self.source)
        self.assertIn('self.workflow_editor.grid_propagate(False)', self.source)
        self.assertIn('workflow=is_workflow_command(command)', self.source)

    def test_workflow_plain_text_mode_replaces_the_child_list(self):
        spec = importlib.util.spec_from_file_location("integrated_term_workflow_plain_text", APP)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        dialog = None
        try:
            child = module.ProcessSpec("Child", "python child.py")
            initial = module.ProcessSpec("Flow", "[Child]()\n[sleep](1s)", workflow=True)
            dialog = module.WorkflowDialog(root, [child], initial, root_dir=PROJECT_DIR, target="macos")
            dialog.update_idletasks()
            list_width = dialog.items.winfo_width()

            dialog._edit_as_plain_text()
            dialog.update_idletasks()

            self.assertIsInstance(dialog.plain_text, tk.Text)
            self.assertEqual(dialog.plain_text.winfo_width(), list_width)
            content = dialog.plain_text.get("1.0", "end-1c")
            self.assertIn("python child.py\n\nsleep 1", content)
            self.assertEqual(str(dialog.available_items.cget("state")), "disabled")
            self.assertEqual(str(dialog.remove_button.cget("state")), "disabled")
            self.assertEqual(str(dialog.plain_text_button.cget("text")), "以工作流编辑")

            dialog.plain_text.insert("end", "\n# keep this edit")
            dialog._toggle_plain_text_edit()
            dialog.update_idletasks()
            self.assertFalse(dialog.plain_text_mode)
            self.assertEqual(dialog.items.winfo_width(), list_width)
            self.assertEqual(str(dialog.available_items.cget("state")), "normal")
            self.assertEqual(str(dialog.remove_button.cget("state")), "normal")
            self.assertEqual(str(dialog.plain_text_button.cget("text")), "以纯文本编辑")

            dialog._toggle_plain_text_edit()
            self.assertTrue(dialog.plain_text_mode)
            self.assertIn("# keep this edit", dialog.plain_text.get("1.0", "end-1c"))
        finally:
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()
            root.destroy()

    def test_font_awesome_assets_load_as_visible_tk_images(self):
        spec = importlib.util.spec_from_file_location("integrated_term_app", APP)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        try:
            button = module.RoundedButton(root, text="", command=lambda: None, width=38, icon="pin")
            root.update_idletasks()
            image = button._icon_images[("pin", "dark")]
            self.assertEqual((image.width(), image.height()), (18, 18))
            opaque_pixels = sum(
                not int(root.tk.call(str(image), "transparency", "get", x, y))
                for y in range(image.height())
                for x in range(image.width())
            )
            self.assertGreater(opaque_pixels, 0)
        finally:
            root.destroy()

    def test_long_press_shows_restart_and_waits_for_release(self):
        spec = importlib.util.spec_from_file_location("integrated_term_long_press", APP)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        clicked = []
        restarted = []
        try:
            button = module.RoundedButton(
                root, text="全部终止", command=lambda: clicked.append(True), long_press_command=lambda: restarted.append(True),
                long_press_text="全部重启",
            )
            button._press_started = time.monotonic() - 0.4
            button._draw()
            labels = [button.itemcget(item, "text") for item in button.find_all() if button.type(item) == "text"]
            self.assertIn("全部重启", labels)
            self.assertTrue(button.find_withtag("long-press-progress"))
            button._release(None)
            self.assertEqual((clicked, restarted), ([], []))

            button._press_started = time.monotonic() - 0.05
            button._release(None)
            self.assertEqual(clicked, [True])

            button._press_started = time.monotonic() - 1.0
            button._schedule_long_press()
            self.assertEqual(restarted, [])
            button._release(None)
            self.assertEqual(restarted, [True])
        finally:
            root.destroy()

    def test_collapsed_group_keeps_process_rows_in_tree_model(self):
        spec = importlib.util.spec_from_file_location("integrated_term_tree_rows", APP)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        try:
            view = object.__new__(module.App)
            view.lang = "zh"
            view.tree = module.ttk.Treeview(root)
            view.manager = module.ProcessManager(PROJECT_DIR, lambda *_args: None)
            group = view.manager.add_group("collapsed")
            group.expanded = False
            first = view.manager.add(module.ProcessSpec("first", "echo first"), group)
            middle = view.manager.add(module.ProcessSpec("middle", "echo middle"), group)
            last = view.manager.add(module.ProcessSpec("last", "echo last"), group)
            view.selected = None
            view.selected_items = []
            view.selected_group = None
            view.selected_groups = []
            view._refreshing_tree = False
            view._update_action_labels = lambda: None
            view._refresh_process_list_window = lambda: None

            view._refresh_tree()

            self.assertFalse(bool(view.tree.item(group._tree_id, "open")))
            self.assertEqual(len(view.tree.get_children(group._tree_id)), 3)
            row_ids = tuple(view.tree.get_children(group._tree_id))
            middle.return_code = 1
            view._refresh_tree()
            self.assertEqual(tuple(view.tree.get_children(group._tree_id)), row_ids)
            self.assertEqual(len(view.tree.get_children(group._tree_id)), 3)
        finally:
            root.destroy()

    def test_pinned_list_window_has_topmost_lifecycle(self):
        self.assertIn("def _toggle_process_list_window", self.source)
        self.assertIn('window.attributes("-topmost", True)', self.source)
        self.assertIn("def _refresh_process_list_window", self.source)
        self.assertIn("def _close_process_list_window", self.source)
        self.assertIn("def _dialog_parent", self.source)
        self.assertIn("def _present", self.source)
        self.assertIn("self.withdraw()", self.source)
        self.assertIn("self.deiconify()", self.source)
        self.assertIn("self.after_idle(self.focus_force)", self.source)
        app = next(node for node in self.tree.body if isinstance(node, ast.ClassDef) and node.name == "App")
        methods = {node.name for node in app.body if isinstance(node, ast.FunctionDef)}
        self.assertTrue({"_toggle_process_list_window", "_close_process_list_window", "_refresh_process_list_window"} <= methods)

    def test_tree_groups_have_leading_spacing_and_shared_rounded_panel(self):
        self.assertGreaterEqual(self.source.count('text=f"    {marker}  {self._group_name(group)}"'), 2)
        self.assertIn("panel = RoundedPanel(window, padding=10)", self.source)
        self.assertIn("left = RoundedPanel(split)", self.source)
        self.assertNotIn('text="＋ 新建分组"', self.source)
        self.assertNotIn('text="＋ 添加进程"', self.source)
        self.assertIn('style.configure("Treeview"', self.source)
        self.assertIn('borderwidth=0, relief="flat"', self.source)
        self.assertIn('highlightthickness=0', self.source)
        self.assertEqual(self.source.count('font=("SF Pro Display", 12, "bold")'), 2)
        self.assertIn('font=("SF Pro Display", 13, "bold")', self.source)
        self.assertIn('font=("SF Pro Display", 11, "bold")', self.source)
        self.assertEqual(self.source.count('style="SectionTitle.TLabel"'), 3)
        self.assertIn('style.configure("TPanedwindow"', self.source)
        self.assertIn('sashwidth=8', self.source)
        self.assertIn('focus_item: ManagedProcess | None = None', self.source)
        self.assertIn('focus=visible and item is focus_item', self.source)
        self.assertIn('labels = {"auto": ("自动调度", "monitor"), "show": ("保持显示", "monitor"), "hide": ("保持隐藏", "monitor")}', self.source)
        self.assertIn('self.floating_gui_button = RoundedButton(actions, text="自动调度"', self.source)
        self.assertIn('tree.bind("<Double-1>", self._floating_double_click)', self.source)
        self.assertGreaterEqual(self.source.count('bind("<Command-a>", self._select_all_processes)'), 2)
        self.assertGreaterEqual(self.source.count('bind("<Control-a>", self._select_all_processes)'), 2)
        self.assertIn('def _select_all_processes(self, event=None):', self.source)
        self.assertIn('output.xview_moveto(0)', self.source)
        self.assertIn('command=self._toggle_process_terminal_window', self.source)
        self.assertIn('focused_item = next(', self.source)
        self.assertTrue((PROJECT_DIR / "assets" / "icons" / "fa" / "terminal-dark.png").exists())
        self.assertIn('content_left = (self._width - content_width) / 2', self.source)
        self.assertIn('icon_offset = 2', self.source)
        self.assertIn('else -3', self.source)
        self.assertIn('icon_offset = -6 if display_text == "设置根目录"', self.source)
        self.assertIn('if len(display_text) == 4:', self.source)
        self.assertIn('icon_offset = -8', self.source)
        self.assertIn('text="设置根目录"', self.source)
        self.assertIn('self.floating_gui_button', self.source)
        self.assertIn('self.floating_start_button', self.source)
        self.assertIn('self.floating_edit_button', self.source)
        self.assertIn('floating_edit_text = "配置"', self.source)
        self.assertIn('state="normal" if can_edit else "disabled"', self.source)
        self.assertIn('self.floating_start_button.configure(text=start_text, icon=start_icon, long_press_text=restart_text)', self.source)
        self.assertIn('long_press_command=self._restart_processes', self.source)
        self.assertIn('restart_text = "全部重启" if self.selected_groups or len(self.selected_items) > 1 else "重启"', self.source)
        self.assertIn('def _restart_processes(self):', self.source)
        self.assertIn('any_running = any(item.running for item in self.selected_items)', self.source)

    def test_linux_packaging_contract(self):
        packaging = PROJECT_DIR / "packaging" / "linux"
        self.assertTrue((packaging / "integrated-term.spec").exists())
        self.assertTrue((packaging / "build_app.sh").exists())
        self.assertTrue((packaging / "integrated-term.desktop").exists())
        self.assertIn("COLLECT(", (packaging / "integrated-term.spec").read_text())
        self.assertIn("integrated-term-icon-opaque.png", (packaging / "integrated-term.spec").read_text())
        self.assertIn('ProcessDialog(self._dialog_parent()', self.source)
        self.assertIn('GroupRemoveDialog(self._dialog_parent()', self.source)
        self.assertIn('parent=self._dialog_parent()', self.source)
        self.assertGreaterEqual(self.source.count('self.attributes("-topmost", True)'), 2)
        self.assertIn('self.after_idle(self._select)', self.source)
        self.assertIn('text=gui_text, icon=gui_icon', self.source)


if __name__ == "__main__":
    unittest.main()
