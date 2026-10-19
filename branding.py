from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from pathlib import Path

BRAND_COLOR = "#231815"
MARK_PATH = Path(__file__).resolve().parent / "assets" / "active-intelligence-mark@2x.png"


def horizontal_brand(parent: tk.Misc, *, background: str) -> tk.Frame:
    family = tkfont.nametofont("TkDefaultFont", root=parent).cget("family")
    frame = tk.Frame(parent, background=background, borderwidth=0)
    mark = tk.PhotoImage(file=str(MARK_PATH)).subsample(8, 8)
    mark_label = tk.Label(frame, image=mark, background=background, borderwidth=0)
    mark_label.image = mark
    mark_label.pack(side="left", padx=(0, 5))
    tk.Label(
        frame,
        text="主动科技",
        background=background,
        foreground=BRAND_COLOR,
        font=(family, 8, "bold"),
    ).pack(side="left")
    tk.Label(
        frame,
        text="|",
        background=background,
        foreground=BRAND_COLOR,
        font=(family, 12),
        padx=3,
    ).pack(side="left")
    tk.Label(
        frame,
        text="Active Intelligence\nTechnologies",
        background=background,
        foreground=BRAND_COLOR,
        font=(family, 8, "bold"),
        justify="left",
    ).pack(side="left")
    return frame
