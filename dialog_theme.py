"""Scoped action-editor styles matching the keyboard's teal palette."""

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from branding import horizontal_brand

BG = "#e7f3f4"
SURFACE = "#ffffff"
INK = "#104b53"
MUTED = "#64858a"
ACCENT = "#008e85"
BORDER = "#cce2e4"
SELECTED = "#d5efeb"


def _round_rect(canvas, x1, y1, x2, y2, radius=18, **kwargs):
    points = [
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def dialog(parent, title, message, *, confirm=False, language="zh"):
    """Small rounded, themed modal used instead of native platform dialogs."""
    previous_grab = parent.grab_current()
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.grab_set()
    win.configure(background=BG)
    win.resizable(False, False)
    result = {"value": False}
    frame = tk.Frame(win, background=BG, padx=18, pady=18)
    frame.pack()
    horizontal_brand(frame, background=BG).pack(anchor="w", pady=(0, 8))
    canvas = tk.Canvas(frame, width=430, height=150, background=BG, highlightthickness=0)
    canvas.pack()
    canvas.create_text(24, 22, text=title, anchor="nw", fill=INK, font=("sans-serif", -18, "bold"))
    text_id = canvas.create_text(
        24, 56, text=message, anchor="nw", width=380, fill=INK, font=("sans-serif", -17)
    )
    height = max(120, canvas.bbox(text_id)[3] + 24)
    canvas.configure(height=height)
    panel = _round_rect(canvas, 2, 2, 428, height - 2, fill=SURFACE, outline=BORDER, width=1)
    canvas.tag_lower(panel)
    buttons = tk.Frame(frame, background=BG)
    buttons.pack(fill="x", pady=(12, 0))

    def button(text, value, primary=False):
        bg, fg = (ACCENT, "white") if primary else (SURFACE, INK)
        control = tk.Canvas(
            buttons,
            width=96,
            height=38,
            background=BG,
            highlightthickness=0,
            takefocus=True,
            cursor="hand2",
        )
        outline = _round_rect(control, 2, 2, 94, 36, radius=9, fill=bg, outline=BORDER)
        control.bind("<FocusIn>", lambda _e: control.itemconfigure(outline, outline=INK, width=2))
        control.bind(
            "<FocusOut>", lambda _e: control.itemconfigure(outline, outline=BORDER, width=1)
        )
        control.create_text(48, 19, text=text, fill=fg, font=("sans-serif", -17))
        for event in ("<Button-1>", "<Return>", "<space>"):
            control.bind(event, lambda _event: finish(value))
        control.pack(side="right", padx=4)
        return control

    button(
        ("是" if confirm else "确定") if language == "zh" else ("Yes" if confirm else "OK"),
        True,
        primary=True,
    ).focus_set()
    if confirm:
        button("取消" if language == "zh" else "Cancel", False).focus_set()
    win.bind("<Escape>", lambda _event: finish(False))
    win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")

    def finish(value):
        result["value"] = value
        win.grab_release()
        win.destroy()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()

    parent.wait_window(win)
    return result["value"]


def configure_theme(window):
    """Import clam elements locally; never change the application's theme."""
    style = ttk.Style(window)

    def layout(nodes):
        result = []
        for name, options in nodes:
            scoped = "Action." + name
            if scoped not in style.element_names():
                style.element_create(scoped, "from", "clam", name)
            options = dict(options)
            if "children" in options:
                options["children"] = layout(options["children"])
            result.append((scoped, options))
        return result

    for cls in (
        "TFrame",
        "TLabel",
        "TButton",
        "TEntry",
        "TCombobox",
        "TLabelframe",
        "Treeview",
        "Treeview.Heading",
        "Vertical.TScrollbar",
    ):
        style.layout("Action." + cls, layout(style.layout(cls)))
        style.configure(
            "Action." + cls,
            background=SURFACE,
            foreground=INK,
            font=("sans-serif", -17),
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
    style.configure("Action.TFrame", borderwidth=0)
    style.configure("Canvas.Action.TFrame", background=BG)
    style.configure("Canvas.Action.TLabel", background=BG, foreground=MUTED)
    style.configure("Muted.Action.TLabel", foreground=MUTED, font=("sans-serif", -15))
    style.configure("Section.Action.TLabel", font=("sans-serif", -18, "bold"))
    style.configure("Title.Action.TLabel", background=BG, font=("sans-serif", -28, "bold"))
    style.configure("Action.TButton", padding=(12, 8), relief="flat", borderwidth=1)
    style.map("Action.TButton", background=[("disabled", SURFACE), ("!disabled", SURFACE)])
    for variant, _bg, fg, _hover in (
        ("Primary", ACCENT, "white", "#007b74"),
        ("Danger", "#fff4f2", "#b0443d", "#fbe4df"),
    ):
        name = variant + ".Action.TButton"
        style.configure(name, background=SURFACE, foreground=fg)
        style.map(name, background=[("pressed", SURFACE), ("active", SURFACE)])
    style.map("Action.TButton", foreground=[("disabled", "#9ab0b3")])
    for cls in ("TEntry", "TCombobox"):
        name = "Action." + cls
        style.configure(name, padding=6, fieldbackground=SURFACE, arrowsize=14)
        style.map(
            name,
            background=[("disabled", SURFACE), ("!disabled", SURFACE)],
            fieldbackground=[("disabled", "#f5f6f7"), ("readonly", SURFACE)],
            foreground=[("disabled", "#849499"), ("readonly", INK)],
            bordercolor=[("focus", ACCENT)],
            selectbackground=[("!disabled", SELECTED)],
            selectforeground=[("!disabled", INK)],
        )
    style.configure("Action.TLabelframe", borderwidth=1, relief="solid")
    style.configure(
        "Action.TLabelframe.Label",
        background=SURFACE,
        foreground=INK,
        font=("sans-serif", -18, "bold"),
    )
    style.configure("Action.Treeview", fieldbackground=SURFACE, rowheight=42, borderwidth=0)
    style.map(
        "Action.Treeview", background=[("selected", SELECTED)], foreground=[("selected", INK)]
    )
    style.configure(
        "Action.Treeview.Heading",
        background="#f0f7f7",
        padding=(8, 9),
        relief="flat",
        font=("sans-serif", -18, "bold"),
    )
    style.map("Action.Treeview.Heading", background=[("active", SELECTED)])
    style.configure(
        "Action.Vertical.TScrollbar",
        troughcolor=SURFACE,
        background=BORDER,
        arrowcolor=MUTED,
        borderwidth=0,
        arrowsize=12,
    )
    # Image borders stretch only along the straight edges, retaining round corners.
    root = window._root()
    if not hasattr(root, "_action_round_images"):
        assets = Path(__file__).resolve().parent / "assets"
        root._action_round_images = {
            name: tk.PhotoImage(master=root, file=str(assets / f"rounded-{name}.png"))
            for name in (
                "normal",
                "hover",
                "primary",
                "primary-hover",
                "danger",
                "disabled",
                "card",
                "gap",
                "key",
                "focus",
            )
        }
    images = root._action_round_images
    for variant, normal, hover in (
        ("", "normal", "hover"),
        ("Primary.", "primary", "primary-hover"),
        ("Danger.", "danger", "danger"),
    ):
        element = variant + "Action.round"
        if element not in style.element_names():
            style.element_create(
                element,
                "image",
                images[normal],
                ("disabled", images["disabled"]),
                ("pressed", images[hover]),
                ("active", images[hover]),
                ("focus", images[hover]),
                border=10,
                padding=(14, 9),
                sticky="nsew",
            )
        style.layout(
            variant + "Action.TButton",
            [(element, {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})],
        )
    # The transparent image corners expose the ttk widget background, not
    # the parent. Match it explicitly for every state on the teal header.
    for base in ("Action.TButton", "Primary.Action.TButton", "Danger.Action.TButton"):
        name = "Canvas." + base
        style.configure(name, background=BG)
        style.map(name, background=[("disabled", BG), ("!disabled", BG)])

    if "Action.card" not in style.element_names():
        style.element_create(
            "Action.card", "image", images["card"], border=12, padding=16, sticky="nsew"
        )
    style.layout("Card.Action.TFrame", [("Action.card", {"sticky": "nsew"})])
    style.configure("Card.Action.TFrame", background=BG)
    for variant, image in (("Gap", "gap"), ("Key", "key")):
        element = f"Action.{variant}.panel"
        if element not in style.element_names():
            style.element_create(
                element, "image", images[image], border=10, padding=12, sticky="nsew"
            )
        style.layout(f"{variant}.Action.TFrame", [(element, {"sticky": "nsew"})])
        style.configure(f"{variant}.Action.TFrame", background=SURFACE)
    if "Action.roundfield" not in style.element_names():
        style.element_create(
            "Action.roundfield",
            "image",
            images["normal"],
            ("disabled", images["disabled"]),
            ("focus", images["focus"]),
            border=10,
            padding=8,
            sticky="nsew",
        )
    style.layout(
        "Action.TEntry",
        [
            (
                "Action.roundfield",
                {
                    "sticky": "nsew",
                    "children": [
                        (
                            "Entry.padding",
                            {
                                "sticky": "nsew",
                                "children": [("Entry.textarea", {"sticky": "nsew"})],
                            },
                        )
                    ],
                },
            )
        ],
    )
    if "Action.arrow" not in style.element_names():
        images["arrow"] = tk.PhotoImage(
            master=root,
            file=str(Path(__file__).resolve().parent / "assets" / "dropdown-arrow.png"),
        )
        style.element_create("Action.arrow", "image", images["arrow"], sticky="e")
    style.layout(
        "Action.TCombobox",
        [
            (
                "Action.roundfield",
                {
                    "sticky": "nsew",
                    "children": [
                        ("Action.arrow", {"side": "right", "sticky": "ns"}),
                        (
                            "Combobox.padding",
                            {
                                "sticky": "nsew",
                                "children": [("Combobox.textarea", {"sticky": "nsew"})],
                            },
                        ),
                    ],
                },
            )
        ],
    )
    window.configure(background=BG)
    window.option_add("*TCombobox*Listbox.background", SURFACE)
    window.option_add("*TCombobox*Listbox.foreground", INK)
    window.option_add("*TCombobox*Listbox.selectBackground", SELECTED)
    window.option_add("*TCombobox*Listbox.selectForeground", INK)


def apply_styles(widget):
    """Apply base styles while preserving explicit variants."""
    cls = widget.winfo_class()
    if cls in (
        "TFrame",
        "TLabel",
        "TButton",
        "TEntry",
        "TCombobox",
        "Treeview",
        "TLabelframe",
        "TScrollbar",
    ):
        if "Action." not in widget.cget("style"):
            widget.configure(
                style="Action.Vertical.TScrollbar" if cls == "TScrollbar" else "Action." + cls
            )
    if cls in ("TLabel", "TEntry", "TCombobox"):
        widget.configure(font=ttk.Style(widget).lookup(widget.cget("style"), "font"))
    for child in widget.winfo_children():
        apply_styles(child)



def style_dialog(window):
    """Style only this dialog, without changing the console's global theme."""
    if getattr(window, "_dialog_styled", False):
        return
    window._dialog_styled = True
    configure_theme(window)
    bodies = [child for child in window.winfo_children() if isinstance(child, ttk.Frame)]
    if not bodies:
        return
    body = bodies[0]
    body.configure(style="Card.Action.TFrame", padding=22)
    manager = body.winfo_manager()
    if manager == "grid":
        body.grid_configure(padx=16, pady=16)
    else:
        body.pack_configure(padx=16, pady=16)
    children = body.winfo_children()
    logo = horizontal_brand(body, background=SURFACE)
    if any(child.winfo_manager() == "grid" for child in children):
        for child in children:
            if child.winfo_manager() == "grid":
                child.grid_configure(row=int(child.grid_info()["row"]) + 1)
        logo.grid(row=0, column=0, columnspan=max(1, body.grid_size()[0]),
                  sticky="w", pady=(0, 14))
    else:
        logo.pack(side="top", anchor="w", pady=(0, 14), before=children[0] if children else None)
    apply_styles(window)

    def classic(widget):
        if isinstance(widget, (tk.Frame, tk.Label, tk.Canvas)):
            widget.configure(background=SURFACE)
        if isinstance(widget, tk.Label) and not widget.cget("image"):
            widget.configure(foreground=INK, font=("sans-serif", -17))
        if isinstance(widget, (tk.Text, tk.Listbox)):
            widget.configure(background=SURFACE, foreground=INK, relief="flat",
                             borderwidth=0, highlightthickness=1,
                             highlightbackground=BORDER, highlightcolor=ACCENT,
                             selectbackground=SELECTED, selectforeground=INK,
                             font=("sans-serif", -17))
        for child in widget.winfo_children():
            classic(child)
    classic(body)


class ThemedMessages:
    def _show(self, title, message, parent=None, confirm=False, **_options):
        parent = parent or tk._default_root
        current = parent
        while current is not None and not hasattr(current, "lang"):
            current = getattr(current, "master", None)
        language = getattr(current, "lang", "zh")
        original_bg = parent.cget("background")
        configure_theme(parent)
        parent.configure(background=original_bg)
        return dialog(parent, title, message, confirm=confirm, language=language)

    def askyesno(self, title, message, **options):
        return self._show(title, message, confirm=True, **options)

    def showerror(self, title, message, **options):
        self._show(title, message, **options)
        return "ok"

    showwarning = showerror
    showinfo = showerror


messagebox = ThemedMessages()
