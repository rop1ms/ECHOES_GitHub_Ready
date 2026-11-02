# themes.py
# ──────────────────────────────────────────────────────────────────────────────
#  Themes of the player: Echoes Music (default) / Fluid / Fluid Dark / Winamp / Vinyl glass
# ──────────────────────────────────────────────────────────────────────────────

THEMES = {
    # Классическая тёмная тема плеера (бывший «Default» — переименована в
    # «Vinyl glass», т.к. основной темой теперь стала Echoes Music).
    "Vinyl glass": {
        "bg":      "#07080c",
        "panel":   "#0e1018",
        "panel2":  "#13161f",
        "glass":   "rgba(255,255,255,0.04)",
        "glass2":  "rgba(255,255,255,0.07)",
        "text":    "#f2f4fa",
        "muted":   "#6b7280",
        "accent":  "#ffffff",       # белые элементы интерфейса (раньше — фиолетовый/голубой)
        "accent2": "#c9ccd6",
        "border":  "rgba(255,255,255,0.08)",
        "border2": "rgba(255,255,255,0.13)",
        "danger":  "#f87171",
    },
}

# ── Fluid: бежевый фон + жидкая текстура в цвете обложки ─────────────────────
try:
    from fluid_core import fluid_theme_dict
    THEMES["Fluid"] = fluid_theme_dict()
    THEMES["Fluid Dark"] = fluid_theme_dict(dark=True)
except Exception as e:                       # нет numpy / файла — тема просто не появится
    print("[themes] Fluid недоступна:", e)

# ── Winamp: классический Winamp 2.x + окно MilkDrop ──────────────────────────
try:
    from winamp_theme import winamp_theme_dict
    THEMES["Winamp"] = winamp_theme_dict()
except Exception as e:                       # нет numpy / файла — тема просто не появится
    print("[themes] Winamp недоступна:", e)

# ── Echoes Music: интерфейс в духе Яндекс Музыки ─────────────────────────────
try:
    from yamusic_theme import ya_theme_dict
    THEMES["Echoes Music"] = ya_theme_dict()
except Exception as e:                       # нет файла — тема просто не появится
    print("[themes] Echoes Music недоступна:", e)

# ── Echoes Studio: студия для битов, сведения, записи голоса и мэшапов ──────
try:
    from daw_meta import studio_theme_dict, THEME_NAME as _STUDIO
    THEMES[_STUDIO] = studio_theme_dict()
except Exception as e:                       # нет numpy/scipy / файла — тема просто не появится
    print("[themes] Echoes Studio недоступна:", e)

# Тема «Loom» (EchoScript) больше не отдельный режим: при первом запуске она и все скрипты
# пользователя переносятся в конструктор тем (layer_fx.migrate_loom_scripts) — «✦ Loom».

from montage_theme import montage_theme_dict
THEMES["ECHOES Montage"] = montage_theme_dict()

CUSTOM_PREFIX = "✦ "                      # темы из конструктора: «✦ Название»


def theme_groups():
    """Echoes Music — основная тема, полные темы следом, Vinyl glass (классический
    плеер) — в конце. Темы из конструктора — отдельным разделом."""
    builtin = [n for n, t in THEMES.items() if not t.get("custom") and not t.get("dsl_user")]
    names = []
    if "Echoes Music" in builtin:
        names.append("Echoes Music")
    names += [n for n in builtin
              if n not in names and (THEMES[n].get("fluid") or THEMES[n].get("winamp") or THEMES[n].get("yamusic")
                                     or THEMES[n].get("daw"))]
    names += [n for n in builtin if n not in names]
    groups = [("ТЕМЫ", "Echoes Music — основной интерфейс; остальные меняют весь вид плеера", names)]
    mine = [n for n, t in THEMES.items() if t.get("custom")]
    if mine:
        groups.append(("МОИ ТЕМЫ", "Темы из конструктора тем (слои, виджеты и скрипты EchoScript)", mine))
    return groups


def register_custom_themes(store, to_theme_data):
    """Темы из конструктора → THEMES («✦ Название»). Возвращает {ключ: id темы}."""
    for k in [k for k, t in THEMES.items() if t.get("custom")]:
        del THEMES[k]
    keys = {}
    for t in store.list():
        key = CUSTOM_PREFIX + t["name"]
        if key in THEMES:
            key = f"{key} ({t['id']})"
        try:
            THEMES[key] = to_theme_data(t)
        except Exception as e:                       # битая тема не должна ронять плеер
            print("[themes] custom theme skipped:", t.get("id"), e)
            continue
        keys[key] = t["id"]
    return keys


GENRE_MAP = {}          # цветовые схемы убраны — подбирать по жанру больше нечего

def genre_theme(genre, fallback):
    g = (genre or "").lower()
    for key, value in GENRE_MAP.items():
        if key in g:
            return value
    return fallback

def build_qss(t):
    if t.get("daw"):                         # у студии свой полный QSS
        from daw_meta import studio_qss
        return studio_qss(t)
    if t.get("yamusic"):                     # у Echoes Music свой полный QSS
        from yamusic_theme import ya_qss
        return ya_qss(t)
    if t.get("winamp"):                      # у Winamp свой полный QSS
        from winamp_theme import winamp_qss
        return winamp_qss(t)
    if t.get("fluid"):                       # у Fluid свой полный QSS
        from fluid_core import fluid_qss
        return fluid_qss(t)
    font_stack = '"SF Pro Display", "Segoe UI Variable", "Inter", "Helvetica Neue", sans-serif'
    # ── параметры тем из конструктора (у остальных — значения по умолчанию = прежний вид) ──
    fam = (t.get("_font_family") or "").replace('"', "")
    if fam:
        font_stack = f'"{fam}", ' + font_stack
    title_fam = (t.get("_title_family") or "").replace('"', "")
    title_ff = f'font-family: "{title_fam}", {font_stack};' if title_fam else ""
    fs = int(t.get("_font_size", 13))
    fw = t.get("_font_weight")
    fw_css = f"font-weight: {int(fw)};" if fw else ""
    ts, tw = int(t.get("_title_size", 19)), int(t.get("_title_weight", 700))
    R = int(t.get("_radius", 32))                  # панели
    B = int(t.get("_btn_radius", 16))              # кнопки, поля, элементы списков
    bw = int(t.get("_border", 1))
    r_sub = round(R * 22 / 32)
    r_icon, r_ghost, r_pop = round(B * 14 / 16), round(B * 12 / 16), round(B * 18 / 16)
    r_play = min(30, round(B * 30 / 16))
    r_chk = min(9, round(B * 9 / 16))
    bst = t.get("_btn_style", "glass")
    play_bg = t["accent"] if t.get("_play_style") == "accent" else t["text"]

    def _px(v):                                     # 13 → 12 и т.п. для мелких подписей
        return max(8, round(v * fs / 13))

    # стеклянная плашка под кнопками управления (тема из конструктора): как боковые панели
    dock_css = (f"\n    QFrame#DockCard {{ background: {t['glass2']}; border: {bw}px solid {t['border']}; "
                f"border-radius: {R}px; }}") if t.get("_dock") else ""

    # Цвет текста ПОВЕРХ акцента выбирается по яркости акцента: в Snow акцент
    # почти чёрный — раньше на нём был почти чёрный текст (невидимый).
    def _on(hex_color):
        c = hex_color.lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return "#08090d" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"
    on_acc = _on(t["accent"])
    on_acc2 = _on(t["accent2"])
    on_play = _on(play_bg)

    # стиль обычных кнопок: фон / рамка / наведение
    if bst == "solid":
        btn_bg, btn_bd, btn_hover_bg, btn_hover_bd = t["panel2"], "transparent", t["border2"], "transparent"
    elif bst == "outline":
        btn_bg, btn_bd, btn_hover_bg, btn_hover_bd = "transparent", t["border2"], t["glass"], t["text"]
    elif bst == "neon":
        btn_bg, btn_bd, btn_hover_bg, btn_hover_bd = "transparent", t["accent"], t["glass2"], t["accent2"]
    elif bst == "flat":
        btn_bg, btn_bd, btn_hover_bg, btn_hover_bd = "transparent", "transparent", t["glass2"], "transparent"
    else:                                           # glass
        btn_bg, btn_bd, btn_hover_bg, btn_hover_bd = t["glass"], t["border"], t["glass2"], t["border2"]
    btn_fg = t["accent"] if bst == "neon" else t["text"]

    # Базовый QSS (общий для всех тем)
    base = f"""
    * {{
        font-family: {font_stack};
        font-size: {fs}px;{fw_css}
        color: {t["text"]};
    }}

    QMainWindow, QWidget#Root {{
        background: {t["bg"]};
    }}

    /* ── Панели ── */
    QFrame#Panel {{
        background: {t["glass2"]};
        border: {bw}px solid {t["border"]};
        border-radius: {R}px;
    }}
    QFrame#SubPanel {{
        background: {t["glass"]};
        border: {bw}px solid {t["border"]};
        border-radius: {r_sub}px;
    }}
    QFrame#CenterStage {{
        background: transparent;
        border: none;
    }}{dock_css}

    /* ── Текст ── */
    QLabel {{ color: {t["text"]}; background: transparent; }}
    QLabel#Section {{
        color: {t["muted"]};{title_ff}
        font-size: {_px(10)}px;
        font-weight: 600;
        letter-spacing: 1.8px;
        text-transform: uppercase;
    }}
    QLabel#Title {{{title_ff}
        font-size: {ts}px;
        font-weight: {tw};
        letter-spacing: -0.5px;
        color: {t["text"]};
    }}
    QLabel#Artist {{
        color: {t["muted"]};
        font-size: {_px(12)}px;
        font-weight: 400;
    }}
    QLabel#Big  {{ {title_ff} font-size: {_px(14)}px; font-weight: 600; }}
    QLabel#Sub  {{ color: {t["muted"]}; font-size: {_px(11)}px; }}
    QLabel#Time {{
        color: {t["muted"]};
        font-size: {_px(11)}px;
        font-weight: 500;
    }}

    /* ── Списки ── */
    QListWidget {{
        background: transparent;
        border: none;
        color: {t["text"]};
        outline: none;
        font-size: {fs}px;
    }}
    QListWidget::item {{
        padding: 9px 12px;
        margin: 2px 4px;
        border-radius: {B}px;
        min-height: 36px;
    }}
    QListWidget::item:hover {{
        background: {t["glass2"]};
    }}
    QListWidget::item:selected {{
        background: {t["accent"]};
        color: {on_acc};
        font-weight: 600;
        border-radius: {B}px;
    }}

    /* ── Текстовые поля ── */
    QTextEdit {{
        background: {t["glass"]};
        border: {bw}px solid {t["border"]};
        border-radius: {r_sub}px;
        color: {t["text"]};
        padding: 16px;
        line-height: 160%;
        selection-background-color: {t["accent"]};
        font-size: {fs}px;
    }}
    QLineEdit {{
        background: {t["glass"]};
        border: {bw}px solid {t["border"]};
        border-radius: {B}px;
        color: {t["text"]};
        padding: 9px 16px;
        selection-background-color: {t["accent"]};
        font-size: {fs}px;
    }}
    QLineEdit:focus {{
        border: {bw}px solid {t["border2"]};
        background: {t["glass2"]};
    }}

    /* ── Комбо / спин ── */
    QComboBox, QSpinBox {{
        background: {t["glass"]};
        border: {bw}px solid {t["border"]};
        border-radius: {B}px;
        color: {t["text"]};
        padding: 8px 14px;
        selection-background-color: {t["accent"]};
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {t["panel2"]};
        border: {bw}px solid {t["border2"]};
        border-radius: {r_pop}px;
        color: {t["text"]};
        selection-background-color: {t["accent"]};
        selection-color: {on_acc};
        padding: 6px;
    }}

    /* ── Кнопки ── */
    QPushButton {{
        background: {btn_bg};
        color: {btn_fg};
        border: {bw}px solid {btn_bd};
        border-radius: {B}px;
        padding: 9px 18px;
        font-weight: 500;
        font-size: {fs}px;
    }}
    QPushButton:hover {{
        background: {btn_hover_bg};
        border-color: {btn_hover_bd};
    }}
    QPushButton:pressed {{
        background: {t["accent"]};
        color: {on_acc};
        border-color: transparent;
    }}
    QPushButton:checked {{
        background: {t["accent"]};
        color: {on_acc};
        border-color: transparent;
        font-weight: 600;
    }}

    QPushButton#PlayBtn {{
        background: {play_bg};
        color: {t["bg"] if play_bg == t["text"] else on_play};
        border: 2px solid {t["border2"]};
        border-radius: {r_play}px;
        min-width: 60px; min-height: 60px;
        max-width: 60px; max-height: 60px;
        font-size: 22px;
        font-weight: 800;
        padding: 0;
    }}
    QPushButton#PlayBtn:hover {{
        background: {t["accent"]};
        color: {on_acc};
        border-color: {t["accent2"]};
    }}
    QPushButton#PlayBtn:pressed {{
        background: {t["accent2"]};
        color: {on_acc2};
        border-color: {t["accent"]};
    }}

    QPushButton#IconBtn {{
        background: {btn_bg};
        border: {bw}px solid {btn_bd};
        border-radius: {r_icon}px;
        min-width: 58px; max-width: 72px;
        min-height: 34px; max-height: 34px;
        padding: 0 10px;
        font-size: {_px(11)}px;
        font-weight: 500;
        color: {t["muted"]};
    }}
    QPushButton#IconBtn:hover {{
        background: {btn_hover_bg};
        border-color: {btn_hover_bd};
        color: {t["text"]};
    }}
    QPushButton#IconBtn:checked {{
        background: {t["glass2"]};
        border: 1px solid {t["accent"]};
        color: {t["accent"]};
        font-weight: 600;
    }}

    QPushButton#AccentBtn {{
        background: {t["accent"]};
        color: {on_acc};
        border: none;
        font-weight: 600;
        border-radius: {B}px;
    }}
    QPushButton#AccentBtn:hover {{
        background: {t["accent2"]};
        color: {on_acc2};
    }}

    QPushButton#GhostBtn {{
        background: transparent;
        border: {bw}px solid {t["border"]};
        color: {t["muted"]};
        border-radius: {r_ghost}px;
        padding: 6px 14px;
        font-size: {_px(12)}px;
    }}
    QPushButton#GhostBtn:hover {{
        border-color: {t["border2"]};
        color: {t["text"]};
        background: {t["glass"]};
    }}

    /* ── Слайдеры ── */
    QSlider::groove:horizontal {{
        height: 3px;
        background: {t["border"]};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {t["text"]};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 0px; height: 0px;
        margin: 0;
        background: transparent;
        border: none;
    }}
    QSlider::handle:horizontal:hover {{
        width: 14px; height: 14px;
        margin: -6px 0;
        background: {t["text"]};
        border-radius: 7px;
        border: none;
    }}

    QSlider::groove:vertical {{
        width: 3px;
        background: {t["border"]};
        border-radius: 2px;
    }}
    QSlider::sub-page:vertical {{
        background: {t["accent"]};
        border-radius: 2px;
    }}
    QSlider::handle:vertical {{
        width: 0px; height: 0px;
        background: transparent;
        border: none;
    }}
    QSlider::handle:vertical:hover {{
        width: 14px; height: 14px;
        margin: 0 -6px;
        background: {t["text"]};
        border-radius: 7px;
    }}

    /* ── Чекбоксы ── */
    QCheckBox {{
        color: {t["muted"]};
        spacing: 10px;
        font-size: {_px(12)}px;
    }}
    QCheckBox::indicator {{
        width: 18px; height: 18px;
        border-radius: {r_chk}px;
        border: 1px solid {t["border2"]};
        background: {t["glass"]};
    }}
    QCheckBox::indicator:checked {{
        background: {t["accent"]};
        border-color: {t["accent"]};
    }}

    /* ── Табы ── */
    QTabWidget::pane {{ border: none; background: transparent; }}
    QTabBar::tab {{
        background: transparent;
        color: {t["muted"]};
        padding: 7px 18px;
        border-radius: {r_ghost}px;
        font-size: {_px(12)}px;
        font-weight: 500;
        margin: 2px 2px;
    }}
    QTabBar::tab:selected {{
        background: {t["glass2"]};
        color: {t["text"]};
        font-weight: 600;
    }}
    QTabBar::tab:hover {{ color: {t["text"]}; background: {t["glass"]}; }}

    /* ── Скроллбар ── */
    QScrollBar:vertical {{
        background: transparent;
        width: 3px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {t["border2"]};
        border-radius: 2px;
        min-height: 28px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ height: 0; }}
    """
    # элементы, вынутые из раскладки в конструкторе тем (ui_free), — любого размера
    # (16000000, а не 16777215: Qt прибавляет к максимуму отступы и рамку и выходил за предел)
    base += """
    *[echoesFree="true"], QPushButton#PlayBtn[echoesFree="true"], QPushButton#IconBtn[echoesFree="true"],
    QPushButton#GhostBtn[echoesFree="true"] {
        min-width: 0px; min-height: 0px; max-width: 16000000px; max-height: 16000000px;
    }
    """
    if bst == "winamp":
        base += WINAMP_BUTTONS_QSS
    return base


# Кнопки «как в Winamp 2»: серые объёмные (светлый верх-лево, тёмный низ-право), без скруглений,
# нажатие — фаска наоборот. Включается стилем кнопок «Winamp» в конструкторе тем.
_WA_UP = ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dcdce6, stop:0.5 #b9b9c8, stop:1 #8f8fa3);"
          "color: #10101a; border: 1px solid #3c3c4c; border-top-color: #f6f6fc; border-left-color: #f6f6fc;"
          "border-radius: 0px; font-weight: 700;")
_WA_DOWN = ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #8f8fa3, stop:1 #c4c4d2);"
            "color: #10101a; border: 1px solid #f6f6fc; border-top-color: #3c3c4c; border-left-color: #3c3c4c;")
WINAMP_BUTTONS_QSS = f"""
    QPushButton, QPushButton#IconBtn, QPushButton#AccentBtn, QPushButton#GhostBtn, QPushButton#PlayBtn {{ {_WA_UP} }}
    QPushButton#IconBtn {{ padding: 0 8px; }}
    QPushButton:hover, QPushButton#IconBtn:hover, QPushButton#AccentBtn:hover, QPushButton#GhostBtn:hover,
    QPushButton#PlayBtn:hover {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ececf4, stop:0.5 #cacad8, stop:1 #a0a0b4);
        color: #10101a;
    }}
    QPushButton:pressed, QPushButton#IconBtn:pressed, QPushButton#AccentBtn:pressed, QPushButton#GhostBtn:pressed,
    QPushButton#PlayBtn:pressed {{ {_WA_DOWN} }}
    QPushButton:checked, QPushButton#IconBtn:checked {{ {_WA_DOWN} color: #007a00; }}
    QComboBox, QLineEdit, QSpinBox {{
        border-radius: 0px; border: 1px solid #f6f6fc; border-top-color: #3c3c4c; border-left-color: #3c3c4c;
    }}
"""
