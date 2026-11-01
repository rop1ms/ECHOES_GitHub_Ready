# ui_rounding.py
"""Скругления интерфейса, которые не должны теряться при правках чужих модулей.

Подключается один раз при запуске (main.py) и подменяет функции модулей снаружи, не меняя их файлов.

Режим клипа (clip_mode): у кнопок радиус 18–20 px при высоте ~30 px. Qt рисует ПРЯМЫЕ углы, если
радиус больше половины высоты, поэтому кнопки выглядели квадратными. Здесь: радиус не больше 14 px
и высота кнопок не меньше 32 px (радиус всегда влезает), круглее рамка клипа, панели и меню.
"""
from __future__ import annotations

BTN_R = 14            # радиус кнопок (≤ половины высоты 32 px)
BTN_MIN_H = 18        # min-height текста: 18 + отступы 6+6 + рамка 1+1 = 32 px
BAR_R = 22            # верхняя и нижняя панели
FRAME_R = 24          # рамка самого клипа


def install() -> bool:
    try:
        import clip_mode as CM
    except Exception as e:                                 # noqa: BLE001
        print("[ui_rounding]", e)
        return False
    if getattr(CM, "_ui_rounding", False):
        return False
    orig_style, orig_qss = CM.clip_style, CM._qss

    def clip_style(d=None):
        st = orig_style(d)
        if st.get("family") != "winamp":                   # у Winamp углы прямые нарочно
            st["btn_radius"] = min(int(st.get("btn_radius", BTN_R) or 0), BTN_R) or BTN_R
            st["radius"] = max(int(st.get("radius", 0) or 0), BAR_R)
            if st.get("frame", "rounded") == "rounded":
                st["frame_radius"] = max(float(st.get("frame_radius", 0) or 0), FRAME_R)
        return st

    def _qss(st):
        s = orig_qss(st)
        if st.get("family") == "winamp":
            return s
        r = int(st.get("btn_radius", BTN_R) or BTN_R)
        # более поздние правила того же вида перекрывают ранние по каждому свойству
        return s + f"""
    QPushButton {{ border-radius: {r}px; min-height: {BTN_MIN_H}px; }}
    QPushButton#ClipPrimary {{ border-radius: {r}px; }}
    QMenu {{ border-radius: 12px; padding: 6px; }}
    QMenu::item {{ border-radius: 8px; }}
    """

    CM.clip_style = clip_style
    CM._qss = _qss
    CM._ui_rounding = True
    return True
