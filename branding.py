from __future__ import annotations

import tkinter as tk
from pathlib import Path

BRAND_COLOR = "#231815"
MARK_PATH = Path(__file__).resolve().parent / "assets" / "active-intelligence-mark@2x.png"


def horizontal_brand(parent: tk.Misc, *, background: str) -> tk.Frame:
    frame = tk.Frame(parent, background=background, borderwidth=0)
    logo = tk.PhotoImage(master=parent, file=str(
        MARK_PATH.with_name("active-intelligence-lockup.png"))).subsample(3)
    label = tk.Label(frame, image=logo, background=background, borderwidth=0)
    label.image = logo
    label.pack()
    return frame
