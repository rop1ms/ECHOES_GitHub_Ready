# daw_meta.py
"""Описание темы «Echoes Studio» для списка тем — без тяжёлых модулей (звук грузится при входе в тему)."""
from __future__ import annotations

THEME_NAME = "Echoes Studio"


def studio_theme_dict() -> dict:
    return {"bg": "#1c2023", "panel": "#2a3035", "panel2": "#343b41", "glass": "rgba(255,255,255,0.04)",
            "glass2": "rgba(255,255,255,0.08)", "text": "#d2d9de", "muted": "#8d979f", "accent": "#f0a33a",
            "accent2": "#ffc875", "border": "rgba(255,255,255,0.10)", "border2": "rgba(255,255,255,0.16)",
            "danger": "#e8574f", "glow": "#f0a33a", "daw": True}


def studio_qss(_t=None) -> str:
    from daw_ui import QSS
    return QSS + "\nQWidget#Root { background: #1c2023; }\n"
