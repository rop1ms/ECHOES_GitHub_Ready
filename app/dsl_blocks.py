# dsl_blocks.py
"""
Блоки EchoScript «как в Scratch» — визуальный режим редактора кода в конструкторе тем.

Любой код виджета / поведения слоя / скрипта фона показывается блоками и собирается обратно в код:

    код ──parse──▶ дерево (dsl_lang) ──▶ блоки (Blk) ──▶ холст: тянуть, вкладывать, менять
                                              │
    код ◀──────────────── печать ◀────────────┘   (каждая правка блоками сразу пишет код)

* ВСЕ функции языка — блоки: палитра собирается сама из рантайма (dsl_runtime.Scene: 350+ функций)
  и справки (dsl_docs: параметры, значения по умолчанию, описание). Новая функция в рантайме сразу
  появляется блоком.
* Любой вызов — и команда в стопке, и «репортёр» в ячейке другого блока: rect(10, 10, w / 2, …),
  clamp(audio.bass * 2), push(pts, rand()) — функции соединяются как угодно.
* Формы как в Scratch: шапки событий («когда клик»), стопки с выступом, C-блоки (если / пока / для),
  овальные значения и шестиугольные условия. Чего нет в палитре — блок «код» со своим текстом.
* Комментарии кода (// …) переживают перевод в блоки: висят на своих блоках и печатаются обратно.
"""
from __future__ import annotations

import copy
import html as _html
import inspect
import re

from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer, pyqtSignal, QPoint
from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QBrush, QKeySequence,
                         QShortcut, QCursor)
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QMenu, QComboBox, QLabel,
                             QPushButton, QToolTip, QInputDialog, QColorDialog, QApplication)

import dsl_lang as L

# ══════════════════════════════════════════════════════════════════════════ #
#  Категории, события, датчики
# ══════════════════════════════════════════════════════════════════════════ #

CATS = [  # ключ, название, цвет (как в Scratch)
    ("events", "События", "#d9a400"),
    ("control", "Управление", "#e6951a"),
    ("vars", "Переменные", "#ff8c1a"),
    ("layer", "Слой", "#3d7fe0"),
    ("sensing", "Датчики", "#2f9fd0"),
    ("player", "Плеер", "#c45ec4"),
    ("draw", "Рисование", "#8a5cf0"),
    ("shapes", "Формы и картинки", "#7552d0"),
    ("ops", "Операторы", "#4fae4f"),
    ("math", "Математика", "#3a9e44"),
    ("lists", "Списки и текст", "#f0601a"),
    ("widgets", "Виджеты", "#4c86e8"),
    ("time", "Время и анимация", "#0ea882"),
    ("sound", "Звуки", "#c853c8"),
    ("data", "Данные и окна", "#e8566f"),
    ("mine", "Мои функции", "#e8566f"),
    ("other", "Другое", "#7d7f95"),
]
CAT_COLOR = {k: c for k, _t, c in CATS}
CAT_TITLE = {k: t for k, t, _c in CATS}

EVENTS = [("init", "запуск", ""), ("frame", "каждый кадр", "dt"), ("draw", "рисование", ""),
          ("click", "клик", "x, y, b"), ("press", "нажатие мыши", "x, y, b"),
          ("release", "отпускание мыши", "x, y, b"), ("dblclick", "двойной клик", "x, y, b"),
          ("drag", "перетаскивание", "x, y, dx, dy"), ("move", "движение мыши", "x, y"),
          ("enter", "курсор зашёл", ""), ("leave", "курсор ушёл", ""), ("wheel", "колесо мыши", "d, x, y"),
          ("beat", "удар в музыке", "p"), ("track", "смена трека", "t"), ("key", "нажата клавиша", "k"),
          ("keyup", "клавиша отпущена", "k"), ("message", "сообщение", "name, data, from"),
          ("resize", "смена размера", "w, h"), ("destroy", "удаление", "")]
EV_RU = {k: r for k, r, _p in EVENTS}
EV_PARAMS = {k: p for k, _r, p in EVENTS}

SENSING = ["audio.bass", "audio.mid", "audio.high", "audio.level", "audio.peak", "audio.beat", "audio.beat_power",
           "audio.fft", "audio.wave", "track.title", "track.artist", "track.album", "track.cover", "track.progress",
           "track.position", "track.duration", "track.loaded", "track.path", "player.playing", "player.volume",
           "player.shuffle", "player.repeat", "playlist.name", "playlist.index", "playlist.count", "lyrics.line",
           "lyrics.lines", "lyrics.index", "mouse.x", "mouse.y", "mouse.down", "time", "dt", "frame", "W", "H",
           "theme.accent", "theme.accent2", "theme.bg", "theme.text", "theme.muted"]
WIDGET_VARS = ["w", "h", "x", "y", "hover", "pressed", "visible", "name", "self"]

ASSIGN_OPS = ["=", "+=", "-=", "*=", "/=", "%="]
ARITH_OPS = ["+", "-", "*", "/", "%", "**"]
CMP_OPS = ["==", "!=", "<", "<=", ">", ">="]
BIN_OPS = ARITH_OPS + CMP_OPS
_ID = re.compile(r"[^\W\d]\w*$")
_DOTTED = re.compile(r"[^\W\d]\w*(\.[^\W\d]\w*)*$")
_COLOR = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{8}|[0-9a-fA-F]{3})$")
_NUMERIC_P = re.compile(r"^(x|y|w|h|r|rx|ry|r1|r2|[xy]\d|cx|cy|dx|dy|sx|sy|sw|sh|a|b|t|n|i|k|v|d|p|lo|hi|a0|a1|"
                        r"size|sec|dt|frac|freq|dur|rate|volume|angle|rot|strength|sigma|amount|gap|alpha|"
                        r"digits|limit|width|height|min|max|step|speed|deg|rad|value)$")

# ══════════════════════════════════════════════════════════════════════════ #
#  Каталог функций: рантайм + справка
# ══════════════════════════════════════════════════════════════════════════ #

_SECTION_CAT = {"Звук и плеер": "player", "Медиатека": "player", "Рисование": "draw", "Формы и маски": "shapes",
                "Холсты и картинки": "shapes", "Математика": "math", "Язык: ещё": "lists",
                "Вложенные виджеты": "widgets", "Связь виджетов": "widgets", "Время и анимация": "time",
                "Клавиши и мышь": "sensing", "Звук": "sound", "Данные и окна": "data", "Игры": "math",
                "Виджеты": "widgets"}
_LIB_CAT = {"строки": "lists", "списки": "lists", "словари": "lists", "типы": "lists", "JSON": "lists",
            "плавность": "math", "столкнов.": "math", "векторы": "math", "числа": "math", "цвета": "draw",
            "время": "time"}
# функции, которые удобнее держать в «Управлении» (таймеры и функции-обработчики)
_FORCE_CAT = {"after": "control", "every": "control", "cancel": "control", "log": "control", "print": "control",
              "tween": "time", "tween_stop": "time", "artist_shuffle": "player", "artist_menu": "player",
              "restore": "draw", "save": "draw", "translate": "draw", "rotate": "draw", "scale": "draw",
              "shape": "shapes", "canvas": "shapes", "theme_builder": "player", "theme_menu": "player",
              "add_files": "player", "downloader": "player", "profile": "player", "spawn": "widgets",
              "kill": "widgets", "draw_child": "widgets", "layer": "widgets", "layers": "widgets",
              "find_str": "lists", "wrap_words": "lists", "trunc": "math", "log2": "math", "cbrt": "math",
              "is_nan": "math"}


class FnSpec:
    __slots__ = ("name", "params", "variadic", "desc", "cat")

    def __init__(self, name, params, variadic, desc, cat):
        self.name, self.params, self.variadic, self.desc, self.cat = name, params, variadic, desc, cat


_CATALOG: dict | None = None


def _strip(h):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", h))).strip()


def _split_params(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [p.strip() for p in out if p.strip()]


def _py_default(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str) and len(v) < 40:
        return _q(v)
    return None


def _runtime_globals():
    try:
        import dsl_runtime as R
        sc = R.Scene("", R.PlayerBridge(None), R.AudioAnalyzer(None))
        g = sc.g
        return g
    except Exception:                                      # noqa: BLE001
        return {}


def catalog() -> dict:
    """{имя функции: FnSpec} — всё, что можно вызвать из EchoScript."""
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    g = _runtime_globals()
    docs = {}                                              # имя → (параметры | None, описание, категория)
    try:
        import dsl_docs
        for title, body in dsl_docs.SECTIONS:
            if title == "Все функции":
                continue
            cat = _SECTION_CAT.get(title)
            for k, d in re.findall(r"<td class=k>(.*?)</td>\s*<td>(.*?)</td>", body, re.S):
                cell, desc = _strip(k), _strip(d)
                if len(desc) > 220:
                    desc = desc[:217] + "…"
                rest = cell
                for nm, ps in re.findall(r"([^\W\d][\w]*)\(([^()]*)\)", cell):
                    rest = rest.replace(f"{nm}({ps})", " ")
                    if nm in g and nm not in docs:
                        docs[nm] = (ps, desc, cat)
                for nm in re.findall(r"[^\W\d]\w*", rest):
                    if nm in g and callable(g[nm]) and nm not in docs and cat:
                        docs[nm] = (None, desc, cat)
    except Exception:                                      # noqa: BLE001
        pass
    try:                                                   # английский интерфейс — описания из английской справки
        import i18n
        if i18n.LANG == "en":
            import dsl_docs_en
            for _title, body in dsl_docs_en.SECTIONS:
                for k, d in re.findall(r"<td class=k>(.*?)</td>\s*<td>(.*?)</td>", body, re.S):
                    desc = _strip(d)
                    if len(desc) > 220:
                        desc = desc[:217] + "…"
                    for nm in re.findall(r"([^\W\d]\w*)\(", _strip(k)):
                        if nm in docs:
                            ps, _d, cat = docs[nm]
                            docs[nm] = (ps, desc, cat)
    except Exception:                                      # noqa: BLE001
        pass
    libcat = {}
    try:
        import dsl_lib
        grp = None
        for line in (dsl_lib.__doc__ or "").splitlines():
            m = re.match(r"\s{2}(\S+)\s{2,}(.*)$", line)
            if m and m.group(1) in _LIB_CAT:
                grp = _LIB_CAT[m.group(1)]
                words = m.group(2)
            elif grp and re.match(r"\s{6,}\S", line):
                words = line
            else:
                grp = None if not line.strip() else grp
                continue
            for w in re.findall(r"[^\W\d]\w*", words):
                libcat.setdefault(w, grp)
    except Exception:                                      # noqa: BLE001
        pass
    out = {}
    for nm, fn in g.items():
        if nm.startswith("_") or not callable(fn) or nm in ("theme",):
            continue
        py_names, py_var, py_defs = set(), False, {}
        try:
            sig = inspect.signature(fn)
            for p in sig.parameters.values():
                if p.kind in (p.VAR_POSITIONAL,):
                    py_var = True
                elif p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY):
                    py_names.add(p.name)
                if p.default is not p.empty:
                    py_defs[p.name] = _py_default(p.default)
            py_params = [p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_OR_KEYWORD,
                                                                           p.POSITIONAL_ONLY)]
        except (TypeError, ValueError):
            py_params, py_var = [], True
        ps_doc, desc, cat = docs.get(nm, (None, "", None))
        params, variadic = [], False
        if ps_doc is not None:
            for p in _split_params(ps_doc):
                if p.startswith("{") or p.startswith("["):
                    pn, dv = ("opts" if p.startswith("{") else "list"), None
                else:
                    if "…" in p or "..." in p:
                        variadic = True
                        p = p.replace("…", "").replace("...", "").strip()
                        if not p:
                            continue
                    if "=" in p:
                        pn, dv = p.split("=", 1)
                        pn, dv = pn.strip(), dv.strip()
                    else:
                        pn, dv = p, None
                pn = re.sub(r"\s+", "_", pn) or "x"
                params.append({"p": pn, "d": dv if dv is not None else py_defs.get(pn), "kwok": pn in py_names})
        else:
            for p in py_params:
                params.append({"p": p.name, "d": py_defs.get(p.name), "kwok": p.name in py_names})
            variadic = py_var
            if py_var and not params:
                params.append({"p": "", "d": None, "kwok": False})
        cat = _FORCE_CAT.get(nm) or cat or libcat.get(nm) or ("math" if nm.startswith("ease") else "other")
        out[nm] = FnSpec(nm, params, variadic, desc, cat)
    _CATALOG = out
    return out


# ══════════════════════════════════════════════════════════════════════════ #
#  Блок
# ══════════════════════════════════════════════════════════════════════════ #

EXPR_KINDS = {"num", "str", "const", "var", "call", "member", "index", "bin", "logic", "not", "neg", "in", "range",
              "tern", "list", "map", "tpl", "fnlit", "xcode"}
DECL_KINDS = {"prop", "state", "child"}
HAT_KINDS = {"on", "method", "data"}
CAP_KINDS = {"return", "break", "continue"}
BOOL_KINDS = {"logic", "not", "in"}


class Blk:
    def __init__(self, kind, line=0, slots=None, stacks=None, **f):
        self.kind = kind
        self.line = line
        self.slots = list(slots or [])
        self.stacks = [list(s) for s in (stacks or [])]
        self.f = f
        self.note = ""                                     # комментарий в конце строки
        self.pre = []                                      # комментарии над блоком
        self.g = None                                      # геометрия (раскладка)
        self.ax = self.ay = 0.0                            # положение на холсте

    def __deepcopy__(self, memo):
        b = Blk(self.kind, self.line, [copy.deepcopy(s, memo) for s in self.slots],
                [[copy.deepcopy(x, memo) for x in st] for st in self.stacks], **copy.deepcopy(self.f, memo))
        b.note, b.pre = self.note, list(self.pre)
        return b

    def is_expr(self):
        return self.kind in EXPR_KINDS

    def is_bool(self):
        if self.kind in BOOL_KINDS:
            return True
        if self.kind == "bin" and self.f.get("op") in CMP_OPS:
            return True
        return self.kind == "const" and isinstance(self.f.get("v"), bool)

    def category(self):
        k = self.kind
        if k in HAT_KINDS:
            return "mine" if k == "method" else ("vars" if k == "data" else "events")
        if k in ("if", "while", "for", "return", "break", "continue", "fndef", "block", "try", "match", "tern",
                 "fnlit", "code", "xcode", "add", "theme", "scene"):
            return "control"
        if k in ("let", "assign", "prop", "state", "child", "var"):
            return self.f.get("cat", "vars")
        if k in ("num", "str", "const", "bin", "logic", "not", "neg", "in", "range", "list", "map", "tpl",
                 "member", "index"):
            return "ops"
        if k == "call":
            sp = catalog().get(self.f.get("fn") or "")
            return sp.cat if sp else "mine"
        return "other"


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


def new_call(fn: str, args=None, kws=None, callee=None) -> Blk:
    """Блок вызова: параметры — из каталога (подписи ячеек, значения по умолчанию)."""
    sp = catalog().get(fn) if fn and "." not in fn else None
    metas, slots = [], []
    if sp:
        for p in sp.params:
            metas.append(dict(p))
            slots.append(None)
    args = list(args or [])
    for i, a in enumerate(args):
        if i < len(slots):
            slots[i] = a
        else:
            metas.append({"p": "", "d": None, "kwok": False})
            slots.append(a)
    for k, v in (kws or []):
        j = next((i for i, m in enumerate(metas) if m["p"] == k and m.get("kwok")), None)
        if j is not None and slots[j] is None:
            slots[j] = v
            metas[j]["named"] = True
        else:
            metas.append({"p": k, "d": None, "kwok": True, "kw": k})
            slots.append(v)
    var = bool(sp.variadic) if sp else True
    if callee is not None:
        return Blk("call", 0, [callee] + slots, fn="", metas=metas, var=var)
    return Blk("call", 0, slots, fn=fn, metas=metas, var=var)


# ══════════════════════════════════════════════════════════════════════════ #
#  Код → блоки
# ══════════════════════════════════════════════════════════════════════════ #

def _scan_comments(src: str):
    """{строка: текст} — комментарии // в конце строки и отдельными строками."""
    trail, full = {}, {}
    i, n, line = 0, len(src), 1
    line_has_code = False
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            line_has_code = False
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            txt = src[i + 2:j].strip()
            (trail if line_has_code else full)[line] = txt
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += src.count("\n", i, j)
            i = j
            continue
        if ch in "\"'`":
            j = i + 1
            while j < n and src[j] != ch:
                if src[j] == "\\":
                    j += 1
                elif src[j] == "\n":
                    if ch != "`":
                        break
                    line += 1
                j += 1
            i = j + 1
            line_has_code = True
            continue
        if not ch.isspace():
            line_has_code = True
        i += 1
    return trail, full


def _params_text(params, conv) -> str:
    out = []
    for nm, d in params:
        out.append(nm if d is None else f"{nm} = {gen_expr(conv.expr(d), 1, True)}")
    return ", ".join(out)


class Conv:
    def __init__(self, src_lines=None):
        self.lines = src_lines or []
        self.stmts = []                                    # все блоки-инструкции (для комментариев)

    @staticmethod
    def _path(n):
        if n[0] == "name":
            return n[2]
        if n[0] == "self":
            return "self"
        if n[0] == "member":
            base = Conv._path(n[2])
            return f"{base}.{n[3]}" if base else None
        return None

    def expr(self, n) -> Blk:
        k = n[0]
        if k == "num":
            return Blk("num", v=n[2])
        if k == "str":
            return Blk("str", v=n[2])
        if k == "const":
            return Blk("const", v=n[2])
        if k in ("name", "self"):
            return Blk("var", text=self._path(n))
        if k == "member":
            p = self._path(n)
            if p:
                return Blk("var", text=p)
            return Blk("member", 0, [self.expr(n[2])], name=n[3])
        if k == "index":
            return Blk("index", 0, [self.expr(n[2]), self.expr(n[3])])
        if k == "call":
            cal = n[2]
            p = self._path(cal) if cal[0] in ("name", "member", "self") else None
            args = [self.expr(a) for a in n[3]]
            kws = [(kk, self.expr(v)) for kk, v in n[4]]
            if p:
                return new_call(p, args, kws)
            return new_call("", args, kws, callee=self.expr(cal))
        if k == "bin":
            return Blk("bin", 0, [self.expr(n[3]), self.expr(n[4])], op=n[2])
        if k in ("and", "or"):
            return Blk("logic", 0, [self.expr(n[2]), self.expr(n[3])], op=k)
        if k == "not":
            return Blk("not", 0, [self.expr(n[2])])
        if k == "neg":
            if n[2][0] == "num":
                return Blk("num", v=-n[2][2])
            return Blk("neg", 0, [self.expr(n[2])])
        if k == "in":
            return Blk("in", 0, [self.expr(n[2]), self.expr(n[3])], neg=bool(n[4]))
        if k == "range":
            return Blk("range", 0, [self.expr(n[2]), self.expr(n[3])])
        if k == "tern":
            return Blk("tern", 0, [self.expr(n[2]), self.expr(n[3]), self.expr(n[4])])
        if k == "list":
            return Blk("list", 0, [self.expr(x) for x in n[2]])
        if k == "map":
            return Blk("map", 0, [self.expr(v) for _k, v in n[2]], keys=[str(kk) for kk, _v in n[2]])
        if k == "tpl":
            texts, slots, cur = [], [], ""
            for part in n[2]:
                if part[0] == "str":
                    cur += part[2]
                else:
                    texts.append(cur)
                    cur = ""
                    slots.append(self.expr(part))
            texts.append(cur)
            return Blk("tpl", 0, slots, texts=texts)
        if k == "fnlit":
            return Blk("fnlit", 0, [], [self.body(n[3])], params=_params_text(n[2], self))
        raise L.EchoError(f"неизвестный узел {k}")

    def body(self, nodes) -> list:
        out = []
        for st in nodes:
            out.append(self.stmt(st))
        return out

    def _mark(self, b, ln):
        b.line = ln
        self.stmts.append(b)
        return b

    def stmt(self, n) -> Blk:
        k, ln = n[0], n[1]
        if k == "let":
            return self._mark(Blk("let", 0, [self.expr(n[3]) if n[3] is not None else None], name=n[2]), ln)
        if k == "fndef":
            return self._mark(Blk("fndef", 0, [], [self.body(n[4])], name=n[2], params=_params_text(n[3], self)), ln)
        if k == "if":
            arms = list(n[2])
            els = n[3]
            while els is not None and len(els) == 1 and els[0][0] == "if":   # else if → elif
                arms += list(els[0][2])
                els = els[0][3]
            b = Blk("if", 0, [self.expr(c) for c, _b in arms], [self.body(bb) for _c, bb in arms])
            if els is not None:
                b.stacks.append(self.body(els))
            b.f["els"] = els is not None
            return self._mark(b, ln)
        if k == "while":
            return self._mark(Blk("while", 0, [self.expr(n[2])], [self.body(n[3])]), ln)
        if k == "for":
            return self._mark(Blk("for", 0, [self.expr(n[4])], [self.body(n[5])], a=n[2], b=n[3]), ln)
        if k == "return":
            return self._mark(Blk("return", 0, [self.expr(n[2]) if n[2] is not None else None]), ln)
        if k in ("break", "continue"):
            return self._mark(Blk(k), ln)
        if k == "add":
            return self._mark(Blk("add", 0, [self.expr(n[3])], name=n[2]), ln)
        if k == "try":
            return self._mark(Blk("try", 0, [], [self.body(n[2]), self.body(n[4] or [])], name=n[3] or "",
                                  catch=bool(n[3] or n[4])), ln)
        if k == "match":
            slots, stacks, arms = [self.expr(n[2])], [], []
            for pats, bb in n[3]:
                arms.append(len(pats))
                slots += [self.expr(p) for p in pats]
                stacks.append(self.body(bb))
            if n[4] is not None:
                stacks.append(self.body(n[4]))
            return self._mark(Blk("match", 0, slots, stacks, arms=arms, els=n[4] is not None), ln)
        if k == "block":
            return self._mark(Blk("block", 0, [], [self.body(n[2])]), ln)
        if k == "assign":
            return self._mark(Blk("assign", 0, [self.expr(n[3]), self.expr(n[4])], op=n[2]), ln)
        if k == "expr":
            return self._mark(self.expr(n[2]), ln)
        if k == "theme":
            return self._mark(Blk("theme", 0, [self.expr(n[2])]), ln)
        if k == "scene":
            return self._mark(Blk("scene", 0, [], [self.body(n[2])]), ln)
        raise L.EchoError(f"неизвестная инструкция {k}")

    def widget(self, n, body_only=False) -> dict:
        _k, ln, name, props, states, methods, handlers = n
        scripts = []
        decls = []
        for kind, items in (("prop", props), ("state", states)):
            for nm, ex, dl in items:
                src = self.lines[dl - 1].strip() if 0 < dl <= len(self.lines) else ""
                if kind == "state" and src.startswith("child") and ex[0] == "call" and len(ex[3]) == 2 \
                        and ex[3][0][0] == "str" and ex[3][1][0] == "map":
                    b = Blk("child", 0, [self.expr(ex[3][1])], name=nm, cls=ex[3][0][2])
                else:
                    b = Blk(kind, 0, [self.expr(ex)], name=nm)
                decls.append(self._mark(b, dl))
        if decls:
            decls.sort(key=lambda b: b.line)
            scripts.append({"x": 0, "y": 0, "blocks": [Blk("data")] + decls, "order": 0})
        for nm, params, bb, ml in methods:
            hat = self._mark(Blk("method", 0, name=nm, params=_params_text(params, self)), ml)
            scripts.append({"x": 0, "y": 0, "blocks": [hat] + self.body(bb), "order": ml})
        for ev, params, bb, hl in handlers:
            hat = self._mark(Blk("on", 0, ev=ev, params=_params_text(params, self)), hl)
            scripts.append({"x": 0, "y": 0, "blocks": [hat] + self.body(bb), "order": hl})
        scripts.sort(key=lambda s: s["order"])
        w = {"name": name, "line": ln, "note": "", "pre": [], "tail": [], "scripts": scripts}
        return w


def _attach_comments(conv: Conv, src: str, targets, tail_holder):
    trail, full = _scan_comments(src)
    by_line = {}
    for b in conv.stmts:
        by_line.setdefault(b.line, b)
    for ln, txt in trail.items():
        b = by_line.get(ln)
        if b is not None:
            b.note = txt
    lines = sorted(by_line)
    import bisect
    for ln in sorted(full):
        j = bisect.bisect_right(lines, ln)
        if j < len(lines):
            by_line[lines[j]].pre.append(full[ln])
        elif tail_holder is not None:
            tail_holder.append(full[ln])


def parse_doc(src: str, mode: str) -> dict:
    """Код → документ блоков. mode: "widget" (виджеты + верхний уровень) или "body" (тело виджета:
    поведение слоя / скрипт фона). Синтаксическая ошибка — EchoError."""
    if mode == "body":
        wrapped = "widget __Body {\n" + src + "\n}\n"
        tree = L.parse(wrapped)
        conv = Conv(wrapped.split("\n"))
        w = conv.widget(tree[0], True)
        doc = {"mode": "body", "entries": [("widget", w)]}
        tail = []
        _attach_comments(conv, wrapped, None, tail)
        w["tail"] = tail
        return doc
    tree = L.parse(src)
    conv = Conv(src.split("\n"))
    entries = []
    for n in tree:
        if n[0] == "widget":
            w = conv.widget(n)
            hb = Blk("widget_hdr", n[1])                   # носитель комментариев строки «widget …»
            conv.stmts.append(hb)
            w["hdr"] = hb
            entries.append(("widget", w))
        else:
            entries.append(("stmt", conv.stmt(n)))
    doc = {"mode": "widget", "entries": entries, "tail": []}
    _attach_comments(conv, src, None, doc["tail"])
    return doc


def parse_snippet(text: str) -> list:
    """Кусок кода от пользователя → список скриптов (члены виджета или просто инструкции)."""
    try:
        d = parse_doc(text, "body")
        scr = d["entries"][0][1]["scripts"]
        if scr:
            return scr
    except L.EchoError:
        pass
    tree = L.parse("widget __S {\n on __x {\n" + text + "\n}\n}\n")
    conv = Conv()
    body = conv.body(tree[0][6][0][2])
    return [{"x": 0, "y": 0, "blocks": body}]


def parse_expr_text(text: str) -> Blk:
    p = L.Parser(L.tokenize(text))
    p.skip_nl()
    e = p.expr()
    p.skip_nl()
    if p.peek().kind != "EOF":
        raise L.EchoError("лишнее в выражении")
    return Conv().expr(e)


# ══════════════════════════════════════════════════════════════════════════ #
#  Блоки → код
# ══════════════════════════════════════════════════════════════════════════ #

def _num(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v)) + ".0"
        return repr(v)
    return str(v)


def _prec(b: Blk) -> int:
    k = b.kind
    if k == "tern":
        return 1
    if k == "logic":
        return 2 if b.f.get("op") == "or" else 3
    if k == "not":
        return 4
    if k == "in":
        return 5
    if k == "bin":
        op = b.f.get("op")
        return 5 if op in CMP_OPS else 7 if op in ("+", "-") else 8 if op in ("*", "/", "%") else 10
    if k == "range":
        return 6
    if k == "neg":
        return 9
    if k == "num" and isinstance(b.f.get("v"), (int, float)) and b.f["v"] < 0:
        return 9
    if k in ("call", "member", "index"):
        return 11
    if k == "fnlit":
        return 1
    if k == "xcode":
        return 0
    return 12


def _empty(meta=None, fallback="nil"):
    if meta:
        if meta.get("d") is not None:
            return meta["d"]
        if _NUMERIC_P.match(meta.get("p") or ""):
            return "0"
    return fallback


def gen_expr(b: Blk | None, mn: int = 1, inline: bool = False, empty: str = "nil") -> str:
    if b is None:
        return empty
    s = _gen_expr(b, inline)
    return f"({s})" if _prec(b) < mn else s


def _arg(b, inline, empty="nil"):
    return gen_expr(b, 1, True, empty)


def _gen_expr(b: Blk, inline) -> str:
    k, f, s = b.kind, b.f, b.slots
    if k == "num":
        return _num(f.get("v", 0))
    if k == "str":
        return _q(str(f.get("v", "")))
    if k == "const":
        v = f.get("v")
        return "nil" if v is None else ("true" if v else "false")
    if k == "var":
        return f.get("text") or "nil"
    if k == "xcode":
        return (f.get("text") or "nil").strip() or "nil"
    if k == "member":
        return f"{gen_expr(s[0], 11, inline)}.{f.get('name', 'x')}"
    if k == "index":
        return f"{gen_expr(s[0], 11, inline)}[{_arg(s[1], inline, '0')}]"
    if k == "call":
        metas = f.get("metas") or []
        args = s
        if not f.get("fn"):
            head = gen_expr(s[0], 11, inline)
            args = s[1:]
        else:
            head = f["fn"]
        out, named_mode = [], False
        last = max((i for i, a in enumerate(args) if a is not None), default=-1)

        def meta(j):
            return metas[j] if j < len(metas) else {"p": "", "d": None, "kwok": False}
        for i, a in enumerate(args):
            m = meta(i)
            if m.get("kw"):                                # именованный аргумент, которого нет в подписи
                if a is not None:
                    out.append(f"{m['kw']}: {_arg(a, inline)}")
                    named_mode = True
                continue
            if a is None:
                if i > last:
                    break
                later = [j for j in range(i + 1, len(args)) if args[j] is not None]
                if named_mode or (m.get("d") is not None and
                                  all(meta(j).get("kwok") or meta(j).get("kw") for j in later)):
                    named_mode = True
                    continue
                out.append(_empty(m))
                continue
            if (named_mode or m.get("named")) and m.get("p") and (m.get("kwok") or named_mode):
                out.append(f"{m['p']}: {_arg(a, inline)}")
                named_mode = True
            else:
                out.append(_arg(a, inline, _empty(m)))
        return f"{head}({', '.join(out)})"
    if k == "bin":
        op = f.get("op", "+")
        p = _prec(b)
        if op == "**":
            return f"{gen_expr(s[0], 11, inline, '0')} ** {gen_expr(s[1], 9, inline, '0')}"
        if op in CMP_OPS:
            return f"{gen_expr(s[0], 5, inline, '0')} {op} {gen_expr(s[1], 6, inline, '0')}"
        return f"{gen_expr(s[0], p, inline, '0')} {op} {gen_expr(s[1], p + 1, inline, '0')}"
    if k == "logic":
        op = f.get("op", "and")
        p = _prec(b)
        return f"{gen_expr(s[0], p, inline, 'false')} {op} {gen_expr(s[1], p + 1, inline, 'false')}"
    if k == "not":
        return f"not {gen_expr(s[0], 4, inline, 'false')}"
    if k == "neg":
        return f"-{gen_expr(s[0], 9, inline, '0')}"
    if k == "in":
        return f"{gen_expr(s[0], 5, inline)} {'not in' if f.get('neg') else 'in'} {gen_expr(s[1], 6, inline, '[]')}"
    if k == "range":
        return f"{gen_expr(s[0], 7, inline, '0')}..{gen_expr(s[1], 7, inline, '0')}"
    if k == "tern":
        return f"{gen_expr(s[0], 2, inline, 'false')} ? {gen_expr(s[1], 1, inline)} : {gen_expr(s[2], 1, inline)}"
    if k == "list":
        return "[" + ", ".join(_arg(x, inline) for x in s) + "]"
    if k == "map":
        keys = f.get("keys") or []
        parts = []
        for i, v in enumerate(s):
            kk = keys[i] if i < len(keys) else f"k{i}"
            kk = kk if _ID.match(kk) else _q(kk)
            parts.append(f"{kk}: {_arg(v, inline)}")
        return "{" + ", ".join(parts) + "}"
    if k == "tpl":
        texts = f.get("texts") or [""]
        out = "`"
        for i, t in enumerate(texts):
            out += t.replace("\\", "\\\\").replace("`", "\\`").replace("{", "\\{").replace("}", "\\}")
            if i < len(s):
                out += "{" + gen_expr(s[i], 1, True) + "}"
        return out + "`"
    if k == "fnlit":
        body = b.stacks[0] if b.stacks else []
        return f"fn({f.get('params', '')}) {{ " + "; ".join(gen_inline(x) for x in body) + " }" if body \
            else f"fn({f.get('params', '')}) {{ }}"
    return "nil"


def gen_inline(b: Blk) -> str:
    out = []
    _gen_stmt(b, 0, out, True, None)
    return " ".join(out)


def _body_inline(stack) -> str:
    return "{ " + "; ".join(gen_inline(x) for x in stack) + " }" if stack else "{ }"


class _Out(list):
    """Строки кода + какая строка какому блоку принадлежит (для подсветки ошибок)."""

    def __init__(self):
        super().__init__()
        self.lmap = {}

    def emit(self, text, blk=None):
        self.append(text)
        if blk is not None:
            self.lmap[len(self)] = blk


def _gen_stmt(b: Blk, ind: int, out, inline: bool, lmap_out):
    """Инструкция: inline — одной строкой (внутри скобок переводы строк не значат конец инструкции)."""
    pad = "  " * ind
    k, f, s = b.kind, b.f, b.slots

    def line(text):
        if inline:
            out.append(text)
            return
        for c in b.pre:
            out.emit(f"{pad}// {c}" if c else f"{pad}//")
        out.emit(pad + text + (f"  // {b.note}" if b.note else ""), b)

    def body(stack):
        if inline:
            return _body_inline(stack)
        return None

    def block_lines(head, stack, closing=True):
        if inline:
            out.append(f"{head} {_body_inline(stack)}")
            return
        line(head + " {")
        for x in stack:
            _gen_stmt(x, ind + 1, out, False, lmap_out)
        if closing:
            out.emit(pad + "}")

    if b.is_expr():
        line(gen_expr(b, 13 if b.kind == "map" else (0 if b.kind != "xcode" else 1), inline))
        return
    if k == "let":
        line(f"let {f.get('name') or 'x'}" + (f" = {gen_expr(s[0], 1, inline)}" if s and s[0] is not None else ""))
    elif k == "assign":
        tgt = s[0] if s else None
        if tgt is None or tgt.kind not in ("var", "member", "index") or (tgt.kind == "var" and tgt.f.get("text") in
                                                                          ("self",)):
            if not inline:
                line(f"/* задать: нет переменной */ {gen_expr(s[1] if len(s) > 1 else None, 1, inline)}"
                     if len(s) > 1 and s[1] is not None and s[1].kind == "call" else "/* задать: нет переменной */")
            return
        line(f"{gen_expr(tgt, 11, inline)} {f.get('op', '=')} {gen_expr(s[1] if len(s) > 1 else None, 1, inline)}")
    elif k == "if":
        n = len(s)
        if inline:
            parts = []
            for i in range(n):
                parts.append(f"{'if' if i == 0 else 'elif'} {gen_expr(s[i], 1, True, 'false')} "
                             f"{_body_inline(b.stacks[i] if i < len(b.stacks) else [])}")
            if f.get("els") and len(b.stacks) > n:
                parts.append(f"else {_body_inline(b.stacks[n])}")
            out.append(" ".join(parts))
            return
        line(f"if {gen_expr(s[0] if s else None, 1, False, 'false')} {{")
        for i in range(n):
            if i > 0:
                out.emit(f"{pad}}} elif {gen_expr(s[i], 1, False, 'false')} {{")
            for x in (b.stacks[i] if i < len(b.stacks) else []):
                _gen_stmt(x, ind + 1, out, False, lmap_out)
        if f.get("els") and len(b.stacks) > n:
            out.emit(f"{pad}}} else {{")
            for x in b.stacks[n]:
                _gen_stmt(x, ind + 1, out, False, lmap_out)
        out.emit(pad + "}")
    elif k == "while":
        block_lines(f"while {gen_expr(s[0], 1, inline, 'false')}", b.stacks[0])
    elif k == "for":
        vv = f.get("a") or "i"
        if f.get("b"):
            vv += f", {f['b']}"
        block_lines(f"for {vv} in {gen_expr(s[0], 1, inline, '[]')}", b.stacks[0])
    elif k == "return":
        line("return" + (f" {gen_expr(s[0], 1, inline)}" if s and s[0] is not None else ""))
    elif k in ("break", "continue"):
        line(k)
    elif k == "fndef":
        block_lines(f"fn {f.get('name') or 'f'}({f.get('params', '')})", b.stacks[0])
    elif k == "block":
        if inline:
            out.append(_body_inline(b.stacks[0]))
            return
        line("{")
        for x in b.stacks[0]:
            _gen_stmt(x, ind + 1, out, False, lmap_out)
        out.emit(pad + "}")
    elif k == "try":
        if inline:
            t = f"try {_body_inline(b.stacks[0])}"
            if f.get("catch"):
                t += f" catch {f.get('name') or ''} {_body_inline(b.stacks[1])}".replace("  ", " ")
            out.append(t)
            return
        line("try {")
        for x in b.stacks[0]:
            _gen_stmt(x, ind + 1, out, False, lmap_out)
        if f.get("catch"):
            out.emit(f"{pad}}} catch {(f.get('name') + ' ') if f.get('name') else ''}{{")
            for x in b.stacks[1]:
                _gen_stmt(x, ind + 1, out, False, lmap_out)
        out.emit(pad + "}")
    elif k == "match":
        arms = f.get("arms") or []
        pos = 1
        heads = []
        for na in arms:
            heads.append(", ".join(gen_expr(p, 1, True, "nil") for p in s[pos:pos + na]) or "nil")
            pos += na
        if inline:
            parts = [f"{h} => {_body_inline(b.stacks[i])}" for i, h in enumerate(heads)]
            if f.get("els") and len(b.stacks) > len(arms):
                parts.append(f"else => {_body_inline(b.stacks[len(arms)])}")
            out.append(f"match {gen_expr(s[0], 1, True)} {{ " + "; ".join(parts) + " }")
            return
        line(f"match {gen_expr(s[0], 1, False)} {{")
        for i, h in enumerate(heads):
            out.emit(f"{pad}  {h} => {{")
            for x in b.stacks[i]:
                _gen_stmt(x, ind + 2, out, False, lmap_out)
            out.emit(f"{pad}  }}")
        if f.get("els") and len(b.stacks) > len(arms):
            out.emit(f"{pad}  else => {{")
            for x in b.stacks[len(arms)]:
                _gen_stmt(x, ind + 2, out, False, lmap_out)
            out.emit(f"{pad}  }}")
        out.emit(pad + "}")
    elif k == "add":
        line(f"add {f.get('name') or 'W'} {gen_expr(s[0], 1, inline, '{}') if s and s[0] is not None else '{}'}")
    elif k == "code":
        txt = (f.get("text") or "").strip()
        if txt:
            if inline:
                out.append(txt.replace("\n", "; "))
            else:
                for i, ln in enumerate(txt.split("\n")):
                    if i == 0:
                        line(ln)
                    else:
                        out.emit(pad + ln)
    elif k == "theme":
        line(f"theme {gen_expr(s[0], 1, inline, '{}')}")
    elif k == "scene":
        block_lines("scene", b.stacks[0])
    elif k in DECL_KINDS:
        v = s[0] if s else None
        if k == "child":
            m = gen_expr(v, 1, inline, "{}") if v is not None and v.kind == "map" else "{}"
            line(f"child {f.get('name') or 'c'} = {f.get('cls') or 'Library'} {m}")
        else:
            line(f"{k} {f.get('name') or 'x'} = {gen_expr(v, 1, inline, '0')}")


def gen_widget_body(w: dict, ind: int, out: _Out):
    pad = "  " * ind
    scripts = w["scripts"]                                 # порядок кода — порядок скриптов (как в исходнике)
    decls = []
    for sc in scripts:
        bl = sc["blocks"]
        if bl and bl[0].kind == "data":
            decls += [x for x in bl[1:] if x.kind in DECL_KINDS]
    for d in decls:
        _gen_stmt(d, ind, out, False, None)
    first = not decls
    for sc in scripts:
        bl = sc["blocks"]
        if not bl or bl[0].kind not in ("on", "method"):
            continue
        hat = bl[0]
        if not first:
            out.emit("")
        first = False
        for c in hat.pre:
            out.emit(f"{pad}// {c}")
        p = hat.f.get("params", "")
        if hat.kind == "on":
            head = f"on {hat.f.get('ev') or 'frame'}" + (f"({p})" if p.strip() else "")
        else:
            head = f"fn {hat.f.get('name') or 'f'}({p})"
        body = bl[1:]
        if not body:
            out.emit(f"{pad}{head} {{ }}" + (f"  // {hat.note}" if hat.note else ""), hat)
            continue
        out.emit(f"{pad}{head} {{" + (f"  // {hat.note}" if hat.note else ""), hat)
        for x in body:
            _gen_stmt(x, ind + 1, out, False, None)
        out.emit(pad + "}")
    for c in w.get("tail") or []:
        out.emit(f"{pad}// {c}")


def gen_doc(doc: dict) -> tuple[str, dict]:
    """Документ блоков → (код, {строка: блок})."""
    out = _Out()
    if doc["mode"] == "body":
        gen_widget_body(doc["entries"][0][1], 0, out)
    else:
        for i, (kind, e) in enumerate(doc["entries"]):
            if kind == "stmt":
                if e.kind in ("theme", "scene") and out and out[-1] != "":
                    out.emit("")
                _gen_stmt(e, 0, out, False, None)
                continue
            if out and out[-1] != "":
                out.emit("")
            hdr = e.get("hdr")
            for c in (hdr.pre if hdr else []):
                out.emit(f"// {c}")
            note = hdr.note if hdr else ""
            out.emit(f"widget {e['name']} {{" + (f"   // {note}" if note else ""))
            gen_widget_body(e, 1, out)
            out.emit("}")
        for c in doc.get("tail") or []:
            out.emit(f"// {c}")
    return "\n".join(out).rstrip() + "\n", out.lmap


# ══════════════════════════════════════════════════════════════════════════ #
#  Раскладка: что нарисовано в каждом ряду блока
# ══════════════════════════════════════════════════════════════════════════ #

FONT_PX = 12
PADX = 9
GAP = 5
VPAD = 5
ROW_MIN = 34
REP_MIN = 24
SLOT_H = 20
ARM = 16
MOUTH_MIN = 22
BAR = 20
NOTCH_X = 14
NOTCH_W = 16
NOTCH_D = 5
HAT_TOP = 16

_FONT = None
_FM = None


def _font():
    global _FONT, _FM
    if _FONT is None:
        _FONT = QFont("Segoe UI")
        _FONT.setPixelSize(FONT_PX)
        _FONT.setWeight(QFont.Weight.DemiBold)
        _FM = QFontMetricsF(_FONT)
    return _FONT


def _tw(s):
    _font()
    return _FM.horizontalAdvance(s)


def _shape(b: Blk, ctx: str) -> str:
    if b.kind in HAT_KINDS:
        return "hat"
    if b.kind in CAP_KINDS:
        return "cap"
    if b.kind == "call" and ctx == "stack":
        return "stack"
    if b.is_expr():
        return "bool" if b.is_bool() else "rep"
    return "stack"


def _short(v, n=28):
    s = str(v)
    return s if len(s) <= n else s[:n - 1] + "…"


def rows_of(b: Blk, ctx: str) -> list:
    """Ряды блока: список рядов-списков элементов или ("mouth", i).
    Элементы: ("t", текст) · ("f", поле, текст) · ("d", поле, текст) · ("s", i, подсказка) · ("p", имя) ·
    ("b", действие, знак)."""
    k, f = b.kind, b.f
    if k == "on":
        r = [("t", "когда"), ("d", "ev", EV_RU.get(f.get("ev"), f.get("ev") or "?"))]
        r += [("p", p) for p in _param_names(f.get("params", ""))]
        r.append(("f", "params", "(…)"))
        return [r]
    if k == "method":
        r = [("t", "функция"), ("f", "name", f.get("name") or "f")]
        r += [("p", p) for p in _param_names(f.get("params", ""))]
        r.append(("f", "params", "(…)"))
        return [r]
    if k == "data":
        return [[("t", "данные виджета")]]
    if k in ("prop", "state"):
        return [[("t", "свойство" if k == "prop" else "переменная виджета"), ("f", "name", f.get("name") or "x"),
                 ("t", "="), ("s", 0, "значение")]]
    if k == "child":
        return [[("t", "вложенный"), ("f", "name", f.get("name") or "c"), ("t", "="), ("f", "cls", f.get("cls") or "?"),
                 ("s", 0, "{…}")]]
    if k == "let":
        return [[("t", "пусть"), ("f", "name", f.get("name") or "x"), ("t", "="), ("s", 0, "значение")]]
    if k == "assign":
        return [[("t", "задать"), ("s", 0, "переменная"), ("d", "op", f.get("op", "=")), ("s", 1, "значение")]]
    if k == "if":
        rows = []
        n = len(b.slots)
        for i in range(n):
            rows.append([("t", "если" if i == 0 else "иначе если"), ("s", i, "условие"), ("t", "то")])
            rows.append(("mouth", i))
        if f.get("els"):
            rows.append([("t", "иначе")])
            rows.append(("mouth", n))
        rows[0] = rows[0] + [("b", "elif", "+")]
        return rows
    if k == "while":
        return [[("t", "пока"), ("s", 0, "условие")], ("mouth", 0)]
    if k == "for":
        r = [("t", "для"), ("f", "a", f.get("a") or "i")]
        if f.get("b"):
            r += [("t", ","), ("f", "b", f["b"])]
        r += [("t", "в"), ("s", 0, "список / 0..10")]
        return [r, ("mouth", 0)]
    if k == "return":
        return [[("t", "вернуть"), ("s", 0, "значение")]]
    if k == "break":
        return [[("t", "прервать цикл")]]
    if k == "continue":
        return [[("t", "к следующему шагу")]]
    if k == "fndef":
        return [[("t", "функция"), ("f", "name", f.get("name") or "f"), ("f", "params", f"({f.get('params', '')})")],
                ("mouth", 0)]
    if k == "block":
        return [[("t", "блок")], ("mouth", 0)]
    if k == "try":
        rows = [[("t", "попробовать")], ("mouth", 0)]
        if f.get("catch"):
            rows += [[("t", "если ошибка"), ("f", "name", f.get("name") or "—")], ("mouth", 1)]
        return rows
    if k == "match":
        rows = [[("t", "выбор по"), ("s", 0, "значение"), ("b", "arm", "+")]]
        pos = 1
        for i, na in enumerate(f.get("arms") or []):
            r = [("t", "вариант")]
            for j in range(na):
                if j:
                    r.append(("t", ","))
                r.append(("s", pos + j, "значение"))
            r.append(("b", f"pat{i}", "+"))
            rows += [r, ("mouth", i)]
            pos += na
        if f.get("els"):
            rows += [[("t", "иначе")], ("mouth", len(f.get("arms") or []))]
        return rows
    if k == "add":
        return [[("t", "добавить"), ("f", "name", f.get("name") or "W"), ("s", 0, "{…}")]]
    if k == "code":
        return [[("t", "код"), ("f", "text", _short((f.get("text") or "…").replace("\n", " ⏎ "), 46))]]
    if k == "theme":
        return [[("t", "тема"), ("s", 0, "{…}")]]
    if k == "scene":
        return [[("t", "сцена")], ("mouth", 0)]
    # ── значения ──
    if k == "var":
        return [[("f", "text", f.get("text") or "?")]]
    if k == "num":
        return [[("f", "v", _num(f.get("v", 0)))]]
    if k == "str":
        return [[("f", "v", _short(f.get("v", "")))]]
    if k == "const":
        v = f.get("v")
        return [[("d", "v", "nil" if v is None else ("true" if v else "false"))]]
    if k == "xcode":
        return [[("f", "text", _short(f.get("text") or "…", 40))]]
    if k == "call":
        metas = f.get("metas") or []
        r = []
        if f.get("fn"):
            r.append(("t", f["fn"]))
            off = 0
        else:
            r += [("s", 0, "функция"), ("t", "( )")]
            off = 1
        n = len(b.slots) - off
        for i in range(n):
            m = metas[i] if i < len(metas) else {"p": ""}
            lab = m.get("kw") or m.get("p") or ""
            if lab and n > 1:
                r.append(("l", lab))
                ph = m["d"] if m.get("d") is not None else ""
            else:
                ph = m["d"] if m.get("d") is not None else (m.get("p") or "")
            r.append(("s", i + off, _short(ph, 12)))
        if f.get("var"):
            r.append(("b", "add", "+"))
            if n > 0:
                r.append(("b", "del", "−"))
        return [r]
    if k == "member":
        return [[("s", 0, "объект"), ("t", "."), ("f", "name", f.get("name") or "x")]]
    if k == "index":
        return [[("s", 0, "список"), ("t", "["), ("s", 1, "№"), ("t", "]")]]
    if k == "bin":
        return [[("s", 0, ""), ("d", "op", f.get("op", "+")), ("s", 1, "")]]
    if k == "logic":
        return [[("s", 0, ""), ("d", "op", "и" if f.get("op") == "and" else "или"), ("s", 1, "")]]
    if k == "not":
        return [[("t", "не"), ("s", 0, "")]]
    if k == "neg":
        return [[("t", "−"), ("s", 0, "")]]
    if k == "in":
        return [[("s", 0, ""), ("d", "neg", "не в" if f.get("neg") else "в"), ("s", 1, "список")]]
    if k == "range":
        return [[("s", 0, "от"), ("t", ".."), ("s", 1, "до")]]
    if k == "tern":
        return [[("s", 0, "условие"), ("t", "?"), ("s", 1, "да"), ("t", ":"), ("s", 2, "нет")]]
    if k == "list":
        r = [("t", "список")]
        for i in range(len(b.slots)):
            r.append(("s", i, ""))
        r += [("b", "add", "+")] + ([("b", "del", "−")] if b.slots else [])
        return [r]
    if k == "map":
        r = [("t", "словарь")]
        keys = f.get("keys") or []
        for i in range(len(b.slots)):
            r += [("f", f"key{i}", keys[i] if i < len(keys) else "k"), ("t", ":"), ("s", i, "")]
        r += [("b", "add", "+")] + ([("b", "del", "−")] if b.slots else [])
        return [r]
    if k == "tpl":
        r = [("t", "текст")]
        texts = f.get("texts") or [""]
        for i, t in enumerate(texts):
            if t or i == 0:
                r.append(("f", f"txt{i}", _short(t, 24) if t else "…"))
            if i < len(b.slots):
                r.append(("s", i, ""))
        r += [("b", "add", "+")]
        return [r]
    if k == "fnlit":
        return [[("t", "функция"), ("f", "params", f"({f.get('params', '')})")], ("mouth", 0)]
    return [[("t", k)]]


def _param_names(p: str) -> list:
    out = []
    for part in _split_params(p or ""):
        nm = part.split("=", 1)[0].strip()
        if _ID.match(nm):
            out.append(nm)
    return out


def _is_literal(b):
    return b is not None and b.kind in ("num", "str")


def measure(b: Blk, ctx: str = "stack"):
    """Размер блока и места всех его частей (в единицах холста, от левого верхнего угла)."""
    _font()
    shape = _shape(b, ctx)
    rows = rows_of(b, ctx)
    rep = shape in ("rep", "bool")
    items, mouths, line_rows = [], [], []
    y = HAT_TOP if shape == "hat" else 0
    width = 0.0
    lead = PADX if not rep else (8 if shape == "rep" else 12)
    last_mouth = False
    for row in rows:
        if isinstance(row, tuple) and row[0] == "mouth":
            idx = row[1]
            stack = b.stacks[idx] if idx < len(b.stacks) else []
            sh, sw = 0.0, 0.0
            for c in stack:
                measure(c, "stack")
                sh += c.g["h"]
                sw = max(sw, c.g["w"])
            sh = max(MOUTH_MIN, sh)
            mouths.append((idx, y, sh))
            width = max(width, ARM + sw + 10)
            y += sh
            last_mouth = True
            continue
        last_mouth = False
        x = lead
        sizes = []
        for it in row:
            kind = it[0]
            if kind == "t":
                w_, h_ = _tw(it[1]), FONT_PX + 4
            elif kind == "l":
                w_, h_ = _tw(it[1]) * 0.85, FONT_PX
            elif kind in ("f", "d"):
                w_, h_ = _tw(it[2]) + (22 if kind == "d" else 12), SLOT_H
                if b.kind in ("var",):
                    w_, h_ = _tw(it[2]), FONT_PX + 4
            elif kind == "s":
                ch = b.slots[it[1]] if it[1] < len(b.slots) else None
                if ch is not None and not _is_literal(ch):
                    measure(ch, "slot")
                    w_, h_ = ch.g["w"], ch.g["h"]
                elif ch is not None:
                    txt = _num(ch.f.get("v")) if ch.kind == "num" else _short(ch.f.get("v", ""))
                    w_, h_ = max(24, _tw(txt) + 14 + (14 if ch.kind == "str" and _COLOR.match(str(ch.f.get("v", "")))
                                                       else 0)), SLOT_H
                else:
                    w_, h_ = max(24, _tw(it[2]) * 0.9 + 14), SLOT_H
            elif kind == "p":
                w_, h_ = _tw(it[1]) + 16, SLOT_H
            elif kind == "b":
                w_, h_ = 18, 18
            else:
                w_, h_ = 10, 10
            sizes.append((w_, h_))
        rh = max([h for _w, h in sizes] + [FONT_PX + 4]) + 2 * VPAD
        rh = max(rh, REP_MIN if rep else (ROW_MIN if shape != "hat" else ROW_MIN + 4))
        for it, (w_, h_) in zip(row, sizes):
            r = QRectF(x, y + (rh - h_) / 2, w_, h_)
            items.append((it, r))
            x += w_ + GAP
        x = x - GAP + lead
        line_rows.append((y, rh))
        width = max(width, x)
        y += rh
    if last_mouth:
        line_rows.append((y, BAR))
        y += BAR
    if shape == "hat":
        width = max(width, 120)
    if rep:
        width = max(width, 30)
    else:
        width = max(width, 60 if not mouths else 120)
    b.g = {"w": width, "h": y, "items": items, "mouths": mouths, "rows": line_rows, "shape": shape, "ctx": ctx}
    return b.g


def place(b: Blk, x: float, y: float, sink: list | None = None, depth=0):
    """Абсолютные координаты блока и всех вложенных; sink — список (блок, глубина) для попаданий мышью."""
    b.ax, b.ay = x, y
    if sink is not None:
        sink.append((b, depth))
    for it, r in b.g["items"]:
        if it[0] == "s":
            ch = b.slots[it[1]] if it[1] < len(b.slots) else None
            if ch is not None and not _is_literal(ch):
                place(ch, x + r.x(), y + r.y(), sink, depth + 1)
    for idx, my, mh in b.g["mouths"]:
        cy = y + my
        for c in (b.stacks[idx] if idx < len(b.stacks) else []):
            place(c, x + ARM, cy, sink, depth + 1)
            cy += c.g["h"]


def stack_height(blocks) -> float:
    return sum(c.g["h"] for c in blocks)


# ══════════════════════════════════════════════════════════════════════════ #
#  Рисование
# ══════════════════════════════════════════════════════════════════════════ #

def _notch_top(path: QPainterPath, x0, y, right):
    """Верхний край с выемкой под выступ блока сверху (слева направо)."""
    path.lineTo(x0 + NOTCH_X, y)
    path.lineTo(x0 + NOTCH_X + 4, y + NOTCH_D)
    path.lineTo(x0 + NOTCH_X + NOTCH_W - 4, y + NOTCH_D)
    path.lineTo(x0 + NOTCH_X + NOTCH_W, y)
    path.lineTo(right, y)


def _tab_bottom(path: QPainterPath, x0, y):
    """Нижний край с выступом (справа налево)."""
    path.lineTo(x0 + NOTCH_X + NOTCH_W, y)
    path.lineTo(x0 + NOTCH_X + NOTCH_W - 4, y + NOTCH_D)
    path.lineTo(x0 + NOTCH_X + 4, y + NOTCH_D)
    path.lineTo(x0 + NOTCH_X, y)
    path.lineTo(x0, y)


def block_path(b: Blk) -> QPainterPath:
    g = b.g
    w, h, shape = g["w"], g["h"], g["shape"]
    p = QPainterPath()
    if shape == "rep" and not g["mouths"]:
        p.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        return p
    if shape == "bool":
        e = h / 2
        p.moveTo(e, 0)
        p.lineTo(w - e, 0)
        p.lineTo(w, h / 2)
        p.lineTo(w - e, h)
        p.lineTo(e, h)
        p.lineTo(0, h / 2)
        p.closeSubpath()
        return p
    r = 4.0
    if shape == "hat":
        p.moveTo(0, HAT_TOP)
        p.cubicTo(18, -4, 70, -4, 92, HAT_TOP)
        p.lineTo(w - r, HAT_TOP)
        p.quadTo(w, HAT_TOP, w, HAT_TOP + r)
    else:
        p.moveTo(0, r)
        p.quadTo(0, 0, r, 0)
        if shape == "rep":
            p.lineTo(w - r, 0)
        else:
            _notch_top(p, 0, 0, w - r)
        p.quadTo(w, 0, w, r)
    for idx, my, mh in g["mouths"]:
        p.lineTo(w, my - r)
        p.quadTo(w, my, w - r, my)
        _tab_bottom(p, ARM, my)
        p.lineTo(ARM, my + mh)
        _notch_top(p, ARM, my + mh, w - r)
        p.quadTo(w, my + mh, w, my + mh + r)
    p.lineTo(w, h - r)
    p.quadTo(w, h, w - r, h)
    if shape in ("cap", "rep"):
        p.lineTo(r, h)
    else:
        _tab_bottom(p, 0, h)
        p.lineTo(r, h)
    p.quadTo(0, h, 0, h - r)
    p.closeSubpath()
    return p


def _col(b: Blk) -> QColor:
    return QColor(CAT_COLOR.get(b.category(), "#7d7f95"))


def paint_block(p: QPainter, b: Blk, hl=None, ghost=False):
    """Блок со всем вложенным; hl — {блок: цвет обводки} (ошибка, выбор)."""
    g = b.g
    col = _col(b)
    p.save()
    p.translate(b.ax, b.ay)
    path = block_path(b)
    if ghost:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 60))
        p.drawPath(path)
        p.restore()
        return
    p.setPen(QPen(col.darker(135), 1))
    p.setBrush(col)
    p.drawPath(path)
    if hl and b in hl:
        p.setPen(QPen(QColor(hl[b]), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.setFont(_font())
    dark = col.darker(125)
    for it, r in g["items"]:
        kind = it[0]
        if kind == "t":
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, it[1])
        elif kind == "l":
            f2 = QFont(_font())
            f2.setPixelSize(int(FONT_PX * 0.85))
            f2.setWeight(QFont.Weight.Normal)
            p.setFont(f2)
            p.setPen(QColor(255, 255, 255, 200))
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, it[1])
            p.setFont(_font())
        elif kind in ("f", "d"):
            if b.kind == "var":
                p.setPen(QColor("#ffffff"))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, it[2])
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dark)
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor("#ffffff"))
            if kind == "d":
                p.drawText(r.adjusted(6, 0, -14, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           it[2])
                ax, ay = r.right() - 10, r.center().y()
                tri = QPainterPath()
                tri.moveTo(ax - 4, ay - 2)
                tri.lineTo(ax + 4, ay - 2)
                tri.lineTo(ax, ay + 3)
                tri.closeSubpath()
                p.fillPath(tri, QColor("#ffffff"))
            else:
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, it[2])
        elif kind == "s":
            ch = b.slots[it[1]] if it[1] < len(b.slots) else None
            if ch is not None and not _is_literal(ch):
                continue                                   # вложенный блок рисуется сам
            want_bool = _slot_wants_bool(b, it[1])
            sp = QPainterPath()
            if want_bool and ch is None:
                e = r.height() / 2
                sp.moveTo(r.left() + e, r.top())
                sp.lineTo(r.right() - e, r.top())
                sp.lineTo(r.right(), r.center().y())
                sp.lineTo(r.right() - e, r.bottom())
                sp.lineTo(r.left() + e, r.bottom())
                sp.lineTo(r.left(), r.center().y())
                sp.closeSubpath()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col.darker(140))
                p.drawPath(sp)
                continue
            sp.addRoundedRect(r, r.height() / 2, r.height() / 2)
            p.setPen(QPen(col.darker(140), 1))
            p.setBrush(QColor("#ffffff"))
            p.drawPath(sp)
            if ch is None:
                p.setPen(QColor("#9a9aaa"))
                f2 = QFont(_font())
                f2.setWeight(QFont.Weight.Normal)
                f2.setItalic(True)
                f2.setPixelSize(int(FONT_PX * 0.9))
                p.setFont(f2)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, it[2])
                p.setFont(_font())
            else:
                txt = _num(ch.f.get("v")) if ch.kind == "num" else _short(ch.f.get("v", ""))
                rr = QRectF(r)
                if ch.kind == "str" and _COLOR.match(str(ch.f.get("v", ""))):
                    sw = QRectF(r.left() + 5, r.top() + 4, 11, r.height() - 8)
                    p.setBrush(QColor(str(ch.f["v"])[:7]))
                    p.setPen(QPen(QColor("#00000055"), 1))
                    p.drawRoundedRect(sw, 3, 3)
                    rr = r.adjusted(14, 0, 0, 0)
                p.setPen(QColor("#33334a"))
                p.drawText(rr, Qt.AlignmentFlag.AlignCenter, txt)
        elif kind == "p":
            vc = QColor(CAT_COLOR["vars"])
            p.setPen(QPen(vc.darker(130), 1))
            p.setBrush(vc)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, it[1])
        elif kind == "b":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dark)
            p.drawEllipse(r)
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, it[2])
    if b.note or b.pre:
        note = " / ".join([x for x in b.pre if x] + ([b.note] if b.note else []))
        f2 = QFont(_font())
        f2.setWeight(QFont.Weight.Normal)
        f2.setItalic(True)
        f2.setPixelSize(11)
        p.setFont(f2)
        p.setPen(QColor(220, 220, 235, 150))
        top = HAT_TOP if g["shape"] == "hat" else 0
        p.drawText(QRectF(g["w"] + 8, top, 420, min(g["h"], ROW_MIN)),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "// " + _short(note, 60))
    p.restore()
    for it, _r in g["items"]:
        if it[0] == "s":
            ch = b.slots[it[1]] if it[1] < len(b.slots) else None
            if ch is not None and not _is_literal(ch):
                paint_block(p, ch, hl)
    for idx, _my, _mh in g["mouths"]:
        for c in (b.stacks[idx] if idx < len(b.stacks) else []):
            paint_block(p, c, hl)


def _slot_wants_bool(b: Blk, i: int) -> bool:
    k = b.kind
    if k in ("if", "while") and i < len(b.slots):
        return True
    if k in ("not",):
        return True
    if k == "logic":
        return True
    if k == "tern" and i == 0:
        return True
    return False


def _slot_accepts(b: Blk, i: int, x: Blk) -> bool:
    if b.kind == "assign" and i == 0:
        return x.kind in ("var", "member", "index")
    if b.kind == "child":
        return x.kind == "map"
    return True


# ══════════════════════════════════════════════════════════════════════════ #
#  Холст
# ══════════════════════════════════════════════════════════════════════════ #

def _all_lists(b: Blk):
    for st in b.stacks:
        yield st
        for c in st:
            yield from _all_lists(c)
    for s in b.slots:
        if s is not None:
            yield from _all_lists(s)


class Workspace(QWidget):
    changed = pyqtSignal()

    def __init__(self, editor):
        super().__init__(editor)
        self.ed = editor
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scripts: list = []
        self.zoom = 1.0
        self.scroll = QPointF(-20, -20)
        self.sink: list = []
        self.drag = None                                   # {"script", "off": QPointF, "target"}
        self.press = None
        self.pan = None
        self.hl = {}
        self.error_blk = None
        self.editor_line = None
        self.palette_ref = None
        self._ext = False

    # ── координаты ──
    def to_ws(self, pt) -> QPointF:
        return QPointF(pt.x() / self.zoom + self.scroll.x(), pt.y() / self.zoom + self.scroll.y())

    def set_scripts(self, scripts, arrange=False):
        self.scripts = scripts
        self.relayout()
        if arrange:
            self.arrange()
        self.update()

    @staticmethod
    def _ctx(sc, b):
        return "stack" if (len(sc["blocks"]) > 1 or not b.is_expr() or b.kind == "call") else "float"

    def relayout(self):
        self.sink = []
        for sc in self.scripts:
            y = sc["y"]
            for b in sc["blocks"]:
                measure(b, self._ctx(sc, b))
                place(b, sc["x"], y, self.sink)
                y += b.g["h"]
        if self.drag:
            sc = self.drag["script"]
            y = sc["y"]
            for b in sc["blocks"]:
                measure(b, self._ctx(sc, b))
                place(b, sc["x"], y, None)
                y += b.g["h"]

    def arrange(self):
        """Разложить скрипты колонками: сверху вниз в порядке кода."""
        self.relayout()
        x, y, colw = 0.0, 0.0, 0.0
        total = sum(stack_height(sc["blocks"]) + 30 for sc in self.scripts)
        limit = max(900.0, total / 3)
        for sc in self.scripts:
            h = stack_height(sc["blocks"])
            if y > 0 and y + h > limit:
                x += colw + 60
                y, colw = 0.0, 0.0
            sc["x"], sc["y"] = x, y
            y += h + 30
            colw = max(colw, max((b.g["w"] + (min(300.0, 40 + 6 * len(b.note)) if b.note else 0)
                                  for b in sc["blocks"]), default=0))
        self.scroll = QPointF(-20, -20)
        self.relayout()
        self.update()

    # ── поиск ──
    def _owner(self, target: Blk):
        """(список, индекс) или (блок-родитель, № ячейки) или (скрипт, индекс)."""
        for sc in self.scripts:
            r = self._find_in(sc["blocks"], target, sc)
            if r:
                return r
        return None

    def _find_in(self, lst, target, sc):
        for i, b in enumerate(lst):
            if b is target:
                return ("list", lst, i, sc)
            for j, s in enumerate(b.slots):
                if s is target:
                    return ("slot", b, j, sc)
                if s is not None:
                    r = self._find_in_expr(s, target, sc)
                    if r:
                        return r
            for st in b.stacks:
                r = self._find_in(st, target, sc)
                if r:
                    return r
        return None

    def _find_in_expr(self, b, target, sc):
        for j, s in enumerate(b.slots):
            if s is target:
                return ("slot", b, j, sc)
            if s is not None:
                r = self._find_in_expr(s, target, sc)
                if r:
                    return r
        for st in b.stacks:
            r = self._find_in(st, target, sc)
            if r:
                return r
        return None

    def hit(self, ws: QPointF):
        """(блок, элемент или None) под точкой — самый вложенный."""
        best = None
        for b, depth in self.sink:
            g = b.g
            local = QPointF(ws.x() - b.ax, ws.y() - b.ay)
            lx, ly = local.x(), local.y()
            inside = False
            if g["shape"] in ("rep", "bool") and not g["mouths"]:
                inside = 0 <= lx <= g["w"] and 0 <= ly <= g["h"]
            elif 0 <= lx <= g["w"]:
                inside = any(ry <= ly <= ry + rh for ry, rh in g["rows"])
                if not inside and g["shape"] == "hat":
                    inside = 0 <= ly <= HAT_TOP and lx <= 92
                if not inside:
                    inside = any(my <= ly <= my + mh and lx <= ARM for _i, my, mh in g["mouths"])
            if not inside:
                continue
            item = None
            for it, r in g["items"]:
                if r.adjusted(-2, -2, 2, 2).contains(local):
                    if it[0] == "s":
                        ch = b.slots[it[1]] if it[1] < len(b.slots) else None
                        if ch is not None and not _is_literal(ch):
                            continue
                    item = (it, r)
            if best is None or depth >= best[2]:
                best = (b, item, depth)
        return (best[0], best[1]) if best else (None, None)

    # ── перетаскивание ──
    def _detach(self, b: Blk):
        """Вынуть блок (и всё под ним в стопке) → новый плавающий скрипт."""
        own = self._owner(b)
        if not own:
            return None
        kind, holder, idx, sc = own
        if kind == "slot":
            holder.slots[idx] = None
            return {"x": b.ax, "y": b.ay, "blocks": [b]}
        lst = holder
        if lst is sc["blocks"] and idx == 0:
            sc["_idx"] = self.scripts.index(sc)
            self.scripts.remove(sc)
            return sc
        moved = lst[idx:]
        del lst[idx:]
        return {"x": b.ax, "y": b.ay, "blocks": moved}

    def begin_drag(self, script, grab_ws: QPointF):
        self.drag = {"script": script, "off": QPointF(grab_ws.x() - script["x"], grab_ws.y() - script["y"]),
                     "target": None}
        self.relayout()
        self.update()
        if self.palette_ref is not None:
            self.palette_ref.update()

    def start_external(self, blk: Blk, off: QPointF):
        """Блок из палитры: тянем, пока не отпустят кнопку."""
        self.ed.snapshot()
        ws = self.to_ws(self.mapFromGlobal(QCursor.pos()))
        sc = {"x": ws.x() - off.x(), "y": ws.y() - off.y(), "blocks": [blk]}
        self.drag = {"script": sc, "off": off, "target": None}
        self._ext = True
        self.grabMouse()
        self.relayout()
        self.update()
        if self.palette_ref is not None:
            self.palette_ref.update()

    def _drag_targets(self):
        sc = self.drag["script"]
        first = sc["blocks"][0]
        dragged_ids = set()

        def mark(b):
            dragged_ids.add(id(b))
            for s in b.slots:
                if s is not None:
                    mark(s)
            for st in b.stacks:
                for c in st:
                    mark(c)
        for b in sc["blocks"]:
            mark(b)
        out = []
        one_expr = len(sc["blocks"]) == 1 and first.is_expr()
        tl = QPointF(sc["x"], sc["y"])
        if one_expr:
            lm = QPointF(sc["x"], sc["y"] + first.g["h"] / 2)
            for b, _d in self.sink:
                if id(b) in dragged_ids:
                    continue
                for it, r in b.g["items"]:
                    if it[0] != "s" or not _slot_accepts(b, it[1], first):
                        continue
                    ar = r.translated(b.ax, b.ay)
                    d = ((ar.left() - lm.x()) ** 2 + (ar.center().y() - lm.y()) ** 2) ** 0.5
                    if d < 26:
                        out.append((d, ("slot", b, it[1]), ar))
            if out or first.kind != "call":
                return out
        is_decl = first.kind in DECL_KINDS
        is_hat = first.kind in HAT_KINDS
        dh = stack_height(sc["blocks"])
        for s2 in self.scripts:
            bl = s2["blocks"]
            if not bl:
                continue
            if bl[0].is_expr() and len(bl) == 1 and bl[0].kind != "call":
                continue
            data_list = bl[0].kind == "data"
            if is_hat:
                if bl[0].kind not in HAT_KINDS and not data_list:
                    pt = QPointF(s2["x"], s2["y"] - dh)
                    d = ((pt.x() - tl.x()) ** 2 + (pt.y() - tl.y()) ** 2) ** 0.5
                    if d < 30:
                        out.append((d, ("top", s2), QRectF(s2["x"], s2["y"] - 2, 120, 4)))
                continue
            ok_here = (is_decl == data_list) if bl[0].kind in HAT_KINDS else True
            if ok_here:
                if bl[0].kind not in HAT_KINDS:
                    pt = QPointF(s2["x"], s2["y"] - dh)
                    d = ((pt.x() - tl.x()) ** 2 + (pt.y() - tl.y()) ** 2) ** 0.5
                    if d < 30:
                        out.append((d, ("top", s2), QRectF(s2["x"], s2["y"] - 2, 120, 4)))
                self._list_points(bl, s2["x"], out, tl, dragged_ids, True)
            if not is_decl:
                for b in bl:
                    self._mouth_points(b, out, tl, dragged_ids)
        return out

    def _list_points(self, lst, x, out, tl, dragged, top_level=False):
        for i, b in enumerate(lst):
            if id(b) in dragged:
                return
            if b.kind in CAP_KINDS:
                continue
            pt = QPointF(b.ax, b.ay + b.g["h"])
            d = ((pt.x() - tl.x()) ** 2 + (pt.y() - tl.y()) ** 2) ** 0.5
            if d < 30:
                out.append((d, ("after", lst, i), QRectF(pt.x(), pt.y() - 2, max(80.0, b.g["w"]), 4)))

    def _mouth_points(self, b, out, tl, dragged):
        if id(b) in dragged:
            return
        for idx, my, mh in b.g["mouths"]:
            st = b.stacks[idx]
            pt = QPointF(b.ax + ARM, b.ay + my)
            d = ((pt.x() - tl.x()) ** 2 + (pt.y() - tl.y()) ** 2) ** 0.5
            if d < 30:
                out.append((d, ("mouth", st), QRectF(pt.x(), pt.y() - 2, 100, 4)))
            self._list_points(st, b.ax + ARM, out, tl, dragged)
            for c in st:
                self._mouth_points(c, out, tl, dragged)
        for s in b.slots:
            if s is not None and s.kind == "fnlit":
                self._mouth_points(s, out, tl, dragged)

    def _update_target(self):
        t = self._drag_targets()
        self.drag["target"] = min(t, key=lambda x: x[0]) if t else None

    def _finish_drag(self, gpos):
        d = self.drag
        self.drag = None
        if self._ext:
            self.releaseMouse()
            self._ext = False
        sc = d["script"]
        pal = self.palette_ref
        if pal is not None:
            pal.update()
        if pal is not None and pal.isVisible() and pal.rect().contains(pal.mapFromGlobal(gpos)):
            self.relayout()
            self.update()
            self.changed.emit()                            # бросили на палитру — удалить
            return
        t = d["target"]
        idx0 = sc.pop("_idx", None)
        if t is None:
            if idx0 is not None:
                self.scripts.insert(min(idx0, len(self.scripts)), sc)      # просто передвинули — порядок кода тот же
            else:
                self.scripts.append(sc)
        else:
            kind = t[1][0]
            if kind == "slot":
                _k, b, i = t[1]
                old = b.slots[i]
                b.slots[i] = sc["blocks"][0]
                if old is not None and not _is_literal(old):
                    self.scripts.append({"x": sc["x"] + 30, "y": sc["y"] + 40, "blocks": [old]})
            elif kind == "after":
                _k, lst, i = t[1]
                lst[i + 1:i + 1] = sc["blocks"]
            elif kind == "mouth":
                lst = t[1][1]
                lst[0:0] = sc["blocks"]
            elif kind == "top":
                s2 = t[1][1]
                s2["blocks"][0:0] = sc["blocks"]
                s2["y"] -= stack_height(sc["blocks"])
                s2["x"] = sc["x"] if sc["blocks"][0].kind in HAT_KINDS else s2["x"]
        self.relayout()
        self.update()
        self.changed.emit()

    # ── мышь ──
    def mousePressEvent(self, e):
        self.setFocus()
        self._close_editor()
        ws = self.to_ws(e.position())
        if e.button() == Qt.MouseButton.MiddleButton:
            self.pan = (e.position(), QPointF(self.scroll))
            return
        b, item = self.hit(ws)
        if e.button() == Qt.MouseButton.RightButton:
            self._context(b, e.globalPosition().toPoint(), ws)
            return
        if b is None:
            self.pan = (e.position(), QPointF(self.scroll))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        self.press = (b, item, ws, e.position())

    def mouseMoveEvent(self, e):
        ws = self.to_ws(e.position())
        if self.drag:
            sc = self.drag["script"]
            sc["x"], sc["y"] = ws.x() - self.drag["off"].x(), ws.y() - self.drag["off"].y()
            self.relayout()
            self._update_target()
            self.update()
            return
        if self.pan:
            p0, s0 = self.pan
            d = e.position() - p0
            self.scroll = QPointF(s0.x() - d.x() / self.zoom, s0.y() - d.y() / self.zoom)
            self.relayout()
            self.update()
            return
        if self.press:
            b, item, ws0, p0 = self.press
            if (e.position() - p0).manhattanLength() > 5:
                self.press = None
                self.ed.snapshot()
                if item and item[0][0] == "p":                # параметр из шапки → новая переменная
                    nb = Blk("var", text=item[0][1])
                    measure(nb, "float")
                    self.begin_drag({"x": ws0.x() - 10, "y": ws0.y() - 10, "blocks": [nb]}, ws0)
                    return
                sc = self._detach(b)
                if sc is None:
                    return
                sc["x"], sc["y"] = b.ax, b.ay
                self.begin_drag(sc, ws0)
            return
        b, item = self.hit(ws)
        tip = ""
        if b is not None and b.kind == "call" and b.f.get("fn"):
            sp = catalog().get(b.f["fn"])
            if sp and sp.desc:
                tip = f"{b.f['fn']} — {sp.desc}"
        elif b is not None and b.kind == "on":
            tip = f"on {b.f.get('ev')}: {EV_RU.get(b.f.get('ev'), '')}"
        if tip:
            QToolTip.showText(e.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()

    def mouseReleaseEvent(self, e):
        if self.drag:
            self._finish_drag(e.globalPosition().toPoint())
            return
        if self.pan:
            self.pan = None
            self.unsetCursor()
            return
        if self.press:
            b, item, ws, _p = self.press
            self.press = None
            if item:
                self._click_item(b, item)

    def mouseDoubleClickEvent(self, e):
        pass

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            before = self.to_ws(e.position())
            self.zoom = max(0.4, min(2.2, self.zoom * (1.12 if dy > 0 else 1 / 1.12)))
            after = self.to_ws(e.position())
            self.scroll += before - after
        elif e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.scroll += QPointF(-dy / self.zoom * 0.6, 0)
        else:
            self.scroll += QPointF(-e.angleDelta().x() / self.zoom * 0.6, -dy / self.zoom * 0.6)
        self.ed.zoom_lbl_update()
        self.update()

    def set_zoom(self, z):
        c = self.to_ws(QPointF(self.width() / 2, self.height() / 2))
        self.zoom = max(0.4, min(2.2, z))
        c2 = self.to_ws(QPointF(self.width() / 2, self.height() / 2))
        self.scroll += c - c2
        self.ed.zoom_lbl_update()
        self.update()

    # ── правка полей ──
    def _click_item(self, b: Blk, item):
        it, r = item
        kind = it[0]
        ar = r.translated(b.ax, b.ay)
        if kind == "d":
            self._dropdown(b, it[1], ar)
        elif kind == "f":
            self._edit_field(b, it[1], ar)
        elif kind == "s":
            ch = b.slots[it[1]] if it[1] < len(b.slots) else None
            if ch is not None and ch.kind == "str" and _COLOR.match(str(ch.f.get("v", ""))):
                c = QColorDialog.getColor(QColor(str(ch.f["v"])[:7]), self, "Цвет")
                if c.isValid():
                    self.ed.snapshot()
                    ch.f["v"] = c.name()
                    self._changed()
                return
            self._edit_slot(b, it[1], ar)
        elif kind == "b":
            self.ed.snapshot()
            self._button(b, it[1])
            self._changed()

    def _changed(self):
        self.relayout()
        self.update()
        self.changed.emit()

    def _screen_rect(self, ar: QRectF) -> QRectF:
        tl = QPointF((ar.left() - self.scroll.x()) * self.zoom, (ar.top() - self.scroll.y()) * self.zoom)
        return QRectF(tl.x(), tl.y(), ar.width() * self.zoom, ar.height() * self.zoom)

    def _line_editor(self, ar: QRectF, text, commit):
        self._close_editor()
        sr = self._screen_rect(ar)
        ed = QLineEdit(self)
        ed.setText(text)
        ed.setStyleSheet("QLineEdit { background: #ffffff; color: #1b1b28; border: 2px solid #ffb347;"
                         " border-radius: 8px; padding: 0 6px; font: 600 12px 'Segoe UI'; }")
        ed.setGeometry(int(sr.left()), int(sr.top() - 3), int(max(140, sr.width() + 60)), int(max(24, sr.height() + 6)))
        ed.selectAll()
        ed.show()
        ed.setFocus()
        done = {"v": False}

        def fin():
            if done["v"]:
                return
            done["v"] = True
            val = ed.text()
            ed.deleteLater()
            self.editor_line = None
            commit(val)
        ed.editingFinished.connect(fin)
        self.editor_line = ed

    def _close_editor(self):
        if self.editor_line is not None:
            try:
                self.editor_line.editingFinished.emit()
            except RuntimeError:
                pass
            self.editor_line = None

    def _edit_field(self, b: Blk, key, ar):
        f = b.f
        if key == "params":
            cur = f.get("params", "")
        elif key.startswith("key"):
            i = int(key[3:])
            cur = (f.get("keys") or [""] * (i + 1))[i]
        elif key.startswith("txt"):
            i = int(key[3:])
            cur = (f.get("texts") or [""])[i]
        elif key == "v":
            cur = _num(f.get("v")) if b.kind == "num" else str(f.get("v", ""))
        else:
            cur = str(f.get(key) or "")

        def commit(val):
            self.ed.snapshot()
            if key == "params":
                try:
                    L.parse(f"fn __t({val}) {{ }}")
                    f["params"] = val.strip()
                except L.EchoError:
                    self.ed.flash("Параметры — через запятую: x, y, z = 1")
                    return
            elif key.startswith("key"):
                i = int(key[3:])
                ks = list(f.get("keys") or [])
                while len(ks) <= i:
                    ks.append("k")
                ks[i] = val.strip() or "k"
                f["keys"] = ks
            elif key.startswith("txt"):
                i = int(key[3:])
                ts = list(f.get("texts") or [""])
                ts[i] = val
                f["texts"] = ts
            elif b.kind == "var":
                v = val.strip()
                if _DOTTED.match(v) or v == "self" or v.startswith("self."):
                    f["text"] = v
                elif v:
                    try:
                        nb = parse_expr_text(v)
                        self._replace(b, nb)
                    except L.EchoError:
                        self.ed.flash("Это не имя переменной")
                        return
            elif b.kind in ("num", "str") and key == "v":
                nb = literal_from_text(val, b)
                if nb is not None:
                    self._replace(b, nb)
            elif b.kind == "xcode" or (b.kind == "code" and key == "text"):
                f["text"] = val
            elif key in ("name", "a", "b", "cls"):
                v = val.strip()
                if key == "b" and not v:
                    f["b"] = None
                elif v and _ID.match(v):
                    f[key] = v
                elif key == "name" and b.kind == "try" and not v:
                    f["name"] = ""
                else:
                    self.ed.flash("Имя — буквы, цифры и _, не с цифры")
                    return
            else:
                f[key] = val
            self._changed()
        if b.kind == "code":
            txt, ok = QInputDialog.getMultiLineText(self, "Код", "Своя строка (или несколько) на EchoScript:", cur)
            if ok:
                commit(txt)
            return
        self._line_editor(ar, cur, commit)

    def _replace(self, old: Blk, new: Blk):
        own = self._owner(old)
        if not own:
            return
        kind, holder, idx, sc = own
        if kind == "slot":
            holder.slots[idx] = new
        else:
            holder[idx] = new

    def _edit_slot(self, b: Blk, i, ar):
        ch = b.slots[i] if i < len(b.slots) else None
        cur = ""
        if ch is not None:
            cur = _num(ch.f.get("v")) if ch.kind == "num" else str(ch.f.get("v", ""))

        def commit(val):
            self.ed.snapshot()
            b.slots[i] = literal_from_text(val, ch)
            if b.slots[i] is not None and not _slot_accepts(b, i, b.slots[i]):
                b.slots[i] = None
                self.ed.flash("Сюда — только переменная: перетащите её из «Переменных»")
            self._changed()
        self._line_editor(ar, cur, commit)

    def _dropdown(self, b: Blk, key, ar):
        m = QMenu(self)
        f = b.f
        opts = []
        if key == "ev":
            opts = [(f"{ru}  ({k})", k) for k, ru, _p in EVENTS]
        elif key == "op" and b.kind == "assign":
            opts = [(o, o) for o in ASSIGN_OPS]
        elif key == "op" and b.kind == "bin":
            opts = [(o, o) for o in BIN_OPS]
        elif key == "op" and b.kind == "logic":
            opts = [("и (and)", "and"), ("или (or)", "or")]
        elif key == "neg":
            opts = [("в (in)", False), ("не в (not in)", True)]
        elif key == "v":
            opts = [("true", True), ("false", False), ("nil", None)]
        for title, val in opts:
            a = m.addAction(title)
            a.triggered.connect(lambda _=False, v=val: self._set_drop(b, key, v))
        sr = self._screen_rect(ar)
        m.exec(self.mapToGlobal(QPoint(int(sr.left()), int(sr.bottom()))))

    def _set_drop(self, b, key, v):
        self.ed.snapshot()
        if key == "ev":
            old = b.f.get("ev")
            b.f["ev"] = v
            if not b.f.get("params") or b.f.get("params") == EV_PARAMS.get(old, ""):
                b.f["params"] = EV_PARAMS.get(v, "")
        else:
            b.f[key] = v
        self._changed()

    def _button(self, b: Blk, act):
        if b.kind == "if" and act == "elif":
            n = len(b.slots)
            b.slots.append(None)
            b.stacks.insert(n, [])
            return
        if b.kind == "match":
            arms = list(b.f.get("arms") or [])
            if act == "arm":
                arms.append(1)
                b.slots.append(None)
                b.stacks.insert(len(arms) - 1, [])
            elif act.startswith("pat"):
                i = int(act[3:])
                pos = 1 + sum(arms[:i]) + arms[i]
                b.slots.insert(pos, None)
                arms[i] += 1
            b.f["arms"] = arms
            return
        if act == "add":
            if b.kind == "map":
                ks = list(b.f.get("keys") or [])
                ks.append(f"k{len(ks)}")
                b.f["keys"] = ks
                b.slots.append(None)
            elif b.kind == "tpl":
                b.slots.append(None)
                b.f["texts"] = list(b.f.get("texts") or [""]) + [""]
            else:
                b.slots.append(None)
                if b.kind == "call":
                    b.f.setdefault("metas", []).append({"p": "", "d": None, "kwok": False})
        elif act == "del" and b.slots:
            b.slots.pop()
            if b.kind == "map":
                b.f["keys"] = list(b.f.get("keys") or [])[:len(b.slots)]
            if b.kind == "call":
                b.f["metas"] = list(b.f.get("metas") or [])[:len(b.slots) - (0 if b.f.get("fn") else 1)]

    # ── меню ──
    def _context(self, b: Blk | None, gpos, ws):
        m = QMenu(self)
        if b is not None:
            m.addAction("Дублировать", lambda: self._duplicate(b))
            if b.kind not in HAT_KINDS and self._owner(b) and self._owner(b)[0] == "list":
                m.addAction("Удалить блок", lambda: self._delete_one(b))
            m.addAction("Удалить вместе с блоками ниже", lambda: self._delete_stack(b))
            m.addSeparator()
            m.addAction("Комментарий…", lambda: self._comment(b))
            if b.kind == "if":
                m.addAction("Убрать «иначе»" if b.f.get("els") else "Добавить «иначе»", lambda: self._toggle_else(b))
            if b.kind == "try":
                m.addAction("Убрать «если ошибка»" if b.f.get("catch") else "Добавить «если ошибка»",
                            lambda: self._toggle_catch(b))
            if b.kind == "match":
                m.addAction("Убрать «иначе»" if b.f.get("els") else "Добавить «иначе»", lambda: self._toggle_else(b))
            if b.kind == "for":
                m.addAction("Без второй переменной" if b.f.get("b") else "Две переменные (номер, значение)",
                            lambda: self._toggle_for(b))
            if b.is_expr() or b.kind not in HAT_KINDS:
                m.addAction("Заменить кодом…", lambda: self._as_code(b))
            m.addAction("Скопировать код блока", lambda: QApplication.clipboard().setText(self._code_of(b)))
            if b.kind == "call" and b.f.get("fn") and catalog().get(b.f["fn"]):
                sp = catalog()[b.f["fn"]]
                if sp.desc:
                    a = m.addAction(_short(sp.desc, 80))
                    a.setEnabled(False)
        else:
            m.addAction("Вставить код как блоки…", lambda: self._paste_code(ws))
            m.addAction("Упорядочить блоки", self.arrange)
            m.addAction("Масштаб 100%", lambda: self.set_zoom(1.0))
        m.exec(gpos)

    def _code_of(self, b: Blk) -> str:
        if b.kind in ("on", "method", "data"):
            own = self._owner(b)
            sc = own[3] if own else {"blocks": [b], "x": 0, "y": 0}
            out = _Out()
            gen_widget_body({"scripts": [sc], "tail": []}, 0, out)
            return "\n".join(out)
        if b.is_expr() and self._owner(b) and self._owner(b)[0] == "slot":
            return gen_expr(b, 0)
        out = _Out()
        _gen_stmt(b, 0, out, False, None)
        return "\n".join(out)

    def _duplicate(self, b: Blk):
        self.ed.snapshot()
        own = self._owner(b)
        if own and own[0] == "list":
            _k, lst, i, sc = own
            blocks = copy.deepcopy(lst[i:] if lst is sc["blocks"] and i == 0 or b.kind in HAT_KINDS else [b])
        else:
            blocks = [copy.deepcopy(b)]
        self.scripts.append({"x": b.ax + 30, "y": b.ay + 30, "blocks": blocks})
        self._changed()

    def _delete_one(self, b: Blk):
        self.ed.snapshot()
        own = self._owner(b)
        if not own:
            return
        _k, lst, i, sc = own
        del lst[i]
        if not sc["blocks"]:
            self.scripts.remove(sc)
        self._changed()

    def _delete_stack(self, b: Blk):
        self.ed.snapshot()
        sc = self._detach(b)
        if sc is not None and sc in self.scripts:
            self.scripts.remove(sc)
        self._changed()

    def _comment(self, b: Blk):
        txt, ok = QInputDialog.getText(self, "Комментарий", "Комментарий к блоку (попадёт в код как // …):", text=b.note)
        if ok:
            self.ed.snapshot()
            b.note = txt.strip()
            b.pre = []
            self._changed()

    def _toggle_else(self, b: Blk):
        self.ed.snapshot()
        if b.f.get("els"):
            b.stacks.pop()
            b.f["els"] = False
        else:
            b.stacks.append([])
            b.f["els"] = True
        self._changed()

    def _toggle_catch(self, b: Blk):
        self.ed.snapshot()
        b.f["catch"] = not b.f.get("catch")
        if b.f["catch"] and not b.f.get("name"):
            b.f["name"] = "e"
        while len(b.stacks) < 2:
            b.stacks.append([])
        self._changed()

    def _toggle_for(self, b: Blk):
        self.ed.snapshot()
        b.f["b"] = None if b.f.get("b") else "v"
        self._changed()

    def _as_code(self, b: Blk):
        code = self._code_of(b)
        txt, ok = QInputDialog.getMultiLineText(self, "Код", "Код вместо блока:", code)
        if not ok:
            return
        self.ed.snapshot()
        own = self._owner(b)
        if b.is_expr() and own and own[0] == "slot":
            try:
                nb = parse_expr_text(txt)
            except L.EchoError:
                nb = Blk("xcode", text=txt.strip())
        else:
            nb = Blk("code", text=txt.strip())
        self._replace(b, nb)
        self._changed()

    def _paste_code(self, ws):
        txt, ok = QInputDialog.getMultiLineText(self, "Вставить код как блоки",
                                                "Код EchoScript (on draw { … }, fn …, или просто инструкции):")
        if not ok or not txt.strip():
            return
        try:
            scripts = parse_snippet(txt)
        except L.EchoError as e:
            self.ed.flash(f"Не разобрать: {e}")
            return
        self.ed.snapshot()
        y = ws.y()
        for sc in scripts:
            sc["x"], sc["y"] = ws.x(), y
            for b in sc["blocks"]:
                measure(b)
            y += stack_height(sc["blocks"]) + 30
            self.scripts.append(sc)
        self._changed()

    # ── клавиши ──
    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom * 1.15)
        elif e.key() == Qt.Key.Key_Minus and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom / 1.15)
        else:
            super().keyPressEvent(e)

    # ── рисование ──
    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#16171f"))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        step = 24 * self.zoom
        if step > 8:
            ox = (-self.scroll.x() * self.zoom) % step
            oy = (-self.scroll.y() * self.zoom) % step
            p.setPen(QPen(QColor(255, 255, 255, 16), 2))
            y = oy
            while y < self.height():
                x = ox
                while x < self.width():
                    p.drawPoint(QPointF(x, y))
                    x += step
                y += step
        p.scale(self.zoom, self.zoom)
        p.translate(-self.scroll.x(), -self.scroll.y())
        hl = dict(self.hl)
        if self.error_blk is not None:
            hl[self.error_blk] = "#ff4d5e"
        for sc in self.scripts:
            for b in sc["blocks"]:
                paint_block(p, b, hl)
        if self.drag:
            t = self.drag["target"]
            if t is not None:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 200))
                if t[1][0] == "slot":
                    p.setPen(QPen(QColor("#ffffff"), 2.5))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(t[2].adjusted(-2, -2, 2, 2), 10, 10)
                else:
                    p.drawRoundedRect(t[2].adjusted(0, -1, 0, 1), 3, 3)
            p.setOpacity(0.92)
            for b in self.drag["script"]["blocks"]:
                paint_block(p, b, None)
            p.setOpacity(1)
        if not self.scripts and not self.drag:
            p.resetTransform()
            p.setPen(QColor(255, 255, 255, 90))
            f = QFont("Segoe UI")
            f.setPixelSize(15)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Перетащите сюда блоки из палитры слева:\n«когда рисование» → «fill» → «circle»…")
        p.end()

    def find_block_by_line(self, lmap, line):
        return lmap.get(line)


def literal_from_text(val: str, old: Blk | None) -> Blk | None:
    """Текст из ячейки: число → число, "=выражение" → блоки, иначе строка."""
    v = val
    if not v.strip():
        return None
    s = v.strip()
    if s.startswith("="):
        try:
            return parse_expr_text(s[1:])
        except L.EchoError:
            return Blk("xcode", text=s[1:])
    try:
        if re.fullmatch(r"-?\d+", s):
            return Blk("num", v=int(s))
        if re.fullmatch(r"-?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
            return Blk("num", v=float(s))
    except ValueError:
        pass
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return Blk("str", v=s[1:-1])
    if s in ("true", "false", "nil"):
        return Blk("const", v={"true": True, "false": False, "nil": None}[s])
    return Blk("str", v=v)


# ══════════════════════════════════════════════════════════════════════════ #
#  Палитра
# ══════════════════════════════════════════════════════════════════════════ #

def palette_blocks(role: str, widget_info: dict) -> list:
    """[(ключ категории, [(блок, подсказка)])] — всё, что можно вытащить на холст."""
    cat = catalog()
    out = {k: [] for k, _t, _c in CATS}

    def add(ck, b, tip=""):
        out[ck].append((b, tip))
    for ev, ru, ps in EVENTS:
        add("events", Blk("on", ev=ev, params=ps), f"on {ev}: {ru}")
    add("events", Blk("data"), "Свойства (prop) и переменные (state) виджета")
    # управление
    add("control", Blk("if", 0, [None], [[]], els=False), "если условие — выполнить блоки внутри")
    add("control", Blk("if", 0, [None], [[], []], els=True), "если … иначе …")
    add("control", Blk("while", 0, [None], [[]]), "повторять, пока условие верно")
    add("control", Blk("for", 0, [Blk("range", 0, [Blk("num", v=0), Blk("num", v=10)])], [[]], a="i", b=None),
        "повторить: i = 0, 1, … 9")
    add("control", Blk("for", 0, [None], [[]], a="v", b=None), "для каждого элемента списка")
    add("control", Blk("for", 0, [None], [[]], a="i", b="v"), "номер и значение каждого элемента")
    add("control", Blk("match", 0, [None, None], [[], []], arms=[1], els=True), "выбор варианта (match)")
    add("control", Blk("try", 0, [], [[], []], name="e", catch=True), "ошибка внутри не ломает виджет")
    add("control", Blk("return", 0, [None]), "выйти из обработчика / функции (с значением)")
    add("control", Blk("break"), "выйти из цикла")
    add("control", Blk("continue"), "сразу к следующему шагу цикла")
    for nm in ("after", "every", "cancel", "log"):
        if nm in cat:
            add("control", new_call(nm), cat[nm].desc)
    add("control", Blk("tern", 0, [None, None, None]), "условие ? да : нет")
    add("control", Blk("fnlit", 0, [], [[]], params=""), "функция-значение: для after, every, prompt, map …")
    add("control", Blk("fndef", 0, [], [[]], name="помощник", params=""), "своя функция внутри обработчика")
    add("control", Blk("code", text="log(\"привет\")"), "любой код EchoScript текстом")
    add("control", Blk("xcode", text="w / 2"), "любое выражение текстом")
    # переменные
    add("vars", Blk("data"), "Свойства (prop) и переменные (state) виджета")
    add("vars", Blk("state", 0, [Blk("num", v=0)], name="счёт"), "переменная виджета — живёт между кадрами")
    add("vars", Blk("prop", 0, [Blk("num", v=1)], name="сила"), "свойство — видно в инспекторе слоя")
    add("vars", Blk("child", 0, [Blk("map", 0, [], keys=[])], name="lib", cls="Library"),
        "вложенный виджет (свой класс или блок каталога)")
    add("vars", Blk("let", 0, [Blk("num", v=0)], name="x"), "новая переменная внутри обработчика")
    add("vars", Blk("assign", 0, [Blk("var", text="x"), Blk("num", v=0)], op="="), "задать значение")
    add("vars", Blk("assign", 0, [Blk("var", text="x"), Blk("num", v=1)], op="+="), "изменить на …")
    for nm in widget_info.get("names", []):
        add("vars", Blk("var", text=nm), "переменная этого виджета")
    for nm in WIDGET_VARS:
        add("vars", Blk("var", text=nm), "поле виджета")
    # слой
    for nm in widget_info.get("layer_fields", []):
        add("layer", Blk("assign", 0, [Blk("var", text=nm, cat="layer"), Blk("num", v=0)], op="="), f"поле слоя {nm}")
    for nm in widget_info.get("layer_fields", []):
        add("layer", Blk("var", text=nm, cat="layer"), f"поле слоя {nm}")
    for nm in SENSING:
        add("sensing", Blk("var", text=nm, cat="sensing"), nm)
    # операторы
    for op in ARITH_OPS:
        add("ops", Blk("bin", 0, [None, None], op=op), f"a {op} b")
    for op in CMP_OPS:
        add("ops", Blk("bin", 0, [None, None], op=op), f"a {op} b")
    add("ops", Blk("logic", 0, [None, None], op="and"), "и")
    add("ops", Blk("logic", 0, [None, None], op="or"), "или")
    add("ops", Blk("not", 0, [None]), "не")
    add("ops", Blk("in", 0, [None, None], neg=False), "есть ли в списке / строке / словаре")
    add("ops", Blk("neg", 0, [None]), "минус")
    add("ops", Blk("range", 0, [Blk("num", v=0), Blk("num", v=10)]), "диапазон чисел")
    add("ops", Blk("list", 0, [None, None]), "список")
    add("ops", Blk("map", 0, [None], keys=["name"]), "словарь")
    add("ops", Blk("tpl", 0, [None], texts=["Счёт: ", ""]), "текст с подстановкой `…{x}…`")
    add("ops", Blk("index", 0, [None, Blk("num", v=0)]), "элемент списка по номеру")
    add("ops", Blk("member", 0, [None], name="title"), "поле словаря / виджета")
    add("ops", Blk("str", v="текст"), "строка")
    add("ops", Blk("num", v=0), "число")
    add("ops", Blk("const", v=True), "истина / ложь / пусто")
    # мои функции
    for nm, params in widget_info.get("methods", []):
        pn = _param_names(params)
        add("mine", Blk("call", 0, [None] * len(pn), fn=nm, metas=[{"p": x, "d": None, "kwok": False} for x in pn],
                        var=True), f"своя функция {nm}({params})")
    add("mine", Blk("method", name="действие", params=""), "новая функция виджета")
    add("mine", new_call("моя_функция"), "вызвать любую функцию или метод по имени (имя — щелчком)")
    # все функции рантайма
    for nm in sorted(cat):
        sp = cat[nm]
        if nm in ("after", "every", "cancel", "log"):
            continue
        add(sp.cat if sp.cat in out else "other", new_call(nm), sp.desc)
    if role == "widget":
        out["layer"] = []
    return [(k, out[k]) for k, _t, _c in CATS if out[k]]


class Palette(QWidget):
    """Слева — категории цветными кружками, справа — их блоки; блок тянут на холст."""

    def __init__(self, editor):
        super().__init__(editor)
        self.ed = editor
        self.setMouseTracking(True)
        self.setMinimumWidth(300)
        self.groups = []
        self.entries = []                                  # (rect, блок, подсказка) | (rect, None, заголовок)
        self.cat_rects = []
        self.scroll = 0.0
        self.content_h = 0.0
        self.filter = ""
        self.zoom = 0.86
        self.hover = None

    CAT_W = 68

    def set_groups(self, groups):
        self.groups = groups
        self._layout()
        self.update()

    def set_filter(self, text):
        self.filter = text.strip().lower()
        self.scroll = 0
        self._layout()
        self.update()

    def _layout(self):
        self.entries = []
        y = 8.0
        for ck, items in self.groups:
            shown = []
            for b, tip in items:
                if self.filter:
                    nm = (b.f.get("fn") or b.f.get("text") or b.f.get("ev") or b.kind).lower()
                    if self.filter not in nm and self.filter not in (tip or "").lower():
                        continue
                shown.append((b, tip))
            if not shown:
                continue
            self.entries.append((QRectF(0, y, 300, 26), None, ck))
            y += 30
            for b, tip in shown:
                rep_ = b.is_expr() and not (b.kind == "call" and ck not in ("math", "lists", "ops"))
                measure(b, "slot" if rep_ else "stack")
                h = b.g["h"]
                place(b, 10, y, None)
                self.entries.append((QRectF(10, y, b.g["w"], h), b, tip))
                y += h + 10
            y += 8
        self.content_h = y

    def _px(self):
        return self.zoom

    def jump(self, ck):
        for r, b, t in self.entries:
            if b is None and t == ck:
                self.scroll = max(0.0, min(r.top() - 4, self.content_h - self.height() / self.zoom))
                self.update()
                return

    def wheelEvent(self, e):
        self.scroll = max(0.0, min(self.scroll - e.angleDelta().y() / self.zoom * 0.7,
                                   max(0.0, self.content_h - (self.height() - 0) / self.zoom)))
        self.update()

    def _to_local(self, pos):
        return QPointF((pos.x() - self.CAT_W) / self.zoom, pos.y() / self.zoom + self.scroll)

    def _entry_at(self, pos):
        if pos.x() < self.CAT_W:
            return None
        lp = self._to_local(pos)
        for r, b, t in self.entries:
            if b is not None and r.contains(lp):
                return (r, b, t)
        return None

    def mousePressEvent(self, e):
        pos = e.position()
        if pos.x() < self.CAT_W:
            for r, ck in self.cat_rects:
                if r.contains(pos):
                    self.jump(ck)
            return
        hit = self._entry_at(pos)
        if not hit or e.button() != Qt.MouseButton.LeftButton:
            return
        r, b, _t = hit
        lp = self._to_local(pos)
        off = QPointF(lp.x() - r.left(), lp.y() - r.top())
        self.ed.ws.start_external(copy.deepcopy(b), off)

    def mouseMoveEvent(self, e):
        hit = self._entry_at(e.position())
        if hit and hit[2]:
            QToolTip.showText(e.globalPosition().toPoint(), hit[2], self)
        else:
            QToolTip.hideText()
        cat = None
        if e.position().x() < self.CAT_W:
            for r, ck in self.cat_rects:
                if r.contains(e.position()):
                    cat = ck
        if cat != self.hover:
            self.hover = cat
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#1d1e29"))
        p.fillRect(QRectF(0, 0, self.CAT_W, self.height()), QColor("#14151d"))
        # категории
        self.cat_rects = []
        f = QFont("Segoe UI")
        f.setPixelSize(10)
        p.setFont(f)
        y = 6
        present = [ck for ck, _items in self.groups]
        for ck in present:
            r = QRectF(2, y, self.CAT_W - 4, 56)
            self.cat_rects.append((r, ck))
            if self.hover == ck:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 18))
                p.drawRoundedRect(r, 6, 6)
            c = QColor(CAT_COLOR[ck])
            p.setPen(QPen(c.darker(130), 1))
            p.setBrush(c)
            p.drawEllipse(QPointF(r.center().x(), y + 15), 9, 9)
            p.setPen(QColor("#d6d6e4"))
            p.drawText(QRectF(r.left(), y + 26, r.width(), 28),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                       CAT_TITLE[ck])
            y += 58
        # блоки
        p.save()
        p.setClipRect(QRectF(self.CAT_W, 0, self.width() - self.CAT_W, self.height()))
        p.translate(self.CAT_W, 0)
        p.scale(self.zoom, self.zoom)
        p.translate(0, -self.scroll)
        top, bot = self.scroll - 60, self.scroll + self.height() / self.zoom + 60
        hf = QFont("Segoe UI")
        hf.setPixelSize(14)
        hf.setWeight(QFont.Weight.Bold)
        for r, b, t in self.entries:
            if r.bottom() < top or r.top() > bot:
                continue
            if b is None:
                p.setFont(hf)
                p.setPen(QColor(CAT_COLOR[t]).lighter(130))
                p.drawText(r.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           CAT_TITLE[t])
                continue
            paint_block(p, b, None)
        p.restore()
        if self.ed.ws.drag:
            p.fillRect(self.rect(), QColor(255, 80, 90, 28))
            p.setPen(QColor(255, 200, 205, 200))
            ff = QFont("Segoe UI")
            ff.setPixelSize(14)
            p.setFont(ff)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Отпустите здесь,\nчтобы удалить")
        p.end()


# ══════════════════════════════════════════════════════════════════════════ #
#  Редактор: палитра + холст + панель
# ══════════════════════════════════════════════════════════════════════════ #

QSS = """
QWidget#BlocksBar { background: transparent; }
QLineEdit#BlocksSearch { background: #12131b; color: #e7e5f2; border: 1px solid #2a2e3e; border-radius: 9px;
                         padding: 3px 8px; font: 12px 'Segoe UI'; }
QComboBox { font: 12px 'Segoe UI'; }
QPushButton#BlocksBtn { background: #232534; color: #e7e5f2; border: 1px solid #2f3346; border-radius: 9px;
                        padding: 3px 10px; font: 600 12px 'Segoe UI'; }
QPushButton#BlocksBtn:hover { background: #2e3146; }
QLabel#BlocksInfo { color: #9b9bb4; font: 12px 'Segoe UI'; }
"""


class BlocksEditor(QWidget):
    """Блоки «как в Scratch» для кода слоя. code_changed — новый код после каждой правки блоками."""

    code_changed = pyqtSignal(str)

    def __init__(self, role="widget", layer_fields=None, parent=None):
        super().__init__(parent)
        self.role = role
        self.mode = "widget" if role == "widget" else "body"
        self.layer_fields = list(layer_fields or [])
        self.doc = None
        self.cur_w = None
        self.lmap = {}
        self.last_code = None
        self.undo_stack, self.redo_stack = [], []
        self.setStyleSheet(QSS)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setObjectName("BlocksSearch")
        self.search.setPlaceholderText("Найти блок: circle, клик, громкость…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda t: self.pal.set_filter(t))
        self.search.setMaximumWidth(300)
        self.search.setMinimumWidth(150)
        bar.addWidget(self.search)
        self.wsel = QComboBox()
        self.wsel.setToolTip("Какой виджет показывать блоками (в коде их несколько)")
        self.wsel.currentIndexChanged.connect(self._pick_widget)
        bar.addWidget(self.wsel)
        bar.addStretch(1)
        self.info = QLabel()
        self.info.setObjectName("BlocksInfo")
        from PyQt6.QtWidgets import QSizePolicy
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info.setMinimumWidth(0)
        bar.addWidget(self.info)
        for txt, tip, fn in (("Упорядочить", "Разложить скрипты аккуратными колонками", lambda: self.ws.arrange()),
                             ("−", "Мельче (Ctrl + колесо)", lambda: self.ws.set_zoom(self.ws.zoom / 1.15)),
                             ("100%", "Обычный масштаб", lambda: self.ws.set_zoom(1.0)),
                             ("+", "Крупнее (Ctrl + колесо)", lambda: self.ws.set_zoom(self.ws.zoom * 1.15))):
            b = QPushButton(txt)
            b.setObjectName("BlocksBtn")
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
            if txt == "100%":
                self.zoom_btn = b
        v.addLayout(bar)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.pal = Palette(self)
        self.pal.setFixedWidth(330)
        self.ws = Workspace(self)
        self.ws.palette_ref = self.pal
        self.ws.changed.connect(self._ws_changed)
        row.addWidget(self.pal)
        row.addWidget(self.ws, 1)
        v.addLayout(row, 1)
        self._msg = QTimer(self)
        self._msg.setSingleShot(True)
        self._msg.timeout.connect(lambda: self.info.setText(self._base_info()))
        self._pal_timer = QTimer(self)
        self._pal_timer.setSingleShot(True)
        self._pal_timer.timeout.connect(self._refresh_palette)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)

    # ── загрузка ──
    def load(self, code: str) -> str | None:
        """Код → блоки. Возвращает текст ошибки, если код не разобрать."""
        if self.doc is not None and code == self.last_code:
            return None                                    # код не меняли — оставляем раскладку как была
        try:
            doc = parse_doc(code, self.mode)
        except L.EchoError as e:
            return str(e)
        self.doc = doc
        self.last_code = code
        self.undo_stack.clear()
        self.redo_stack.clear()
        ws_ = [e for k, e in doc["entries"] if k == "widget"]
        self.wsel.blockSignals(True)
        self.wsel.clear()
        for w in ws_:
            self.wsel.addItem(f"Виджет: {w['name']}")
        self.wsel.setVisible(self.mode == "widget" and len(ws_) > 1)
        self.wsel.blockSignals(False)
        if not ws_:                                         # в коде нет виджета — создаём пустой
            w = {"name": "MyWidget", "line": 0, "note": "", "pre": [], "tail": [], "scripts": []}
            doc["entries"].append(("widget", w))
            ws_ = [w]
        self.cur_w = ws_[0]
        self.ws.set_scripts(self.cur_w["scripts"], arrange=True)
        self._refresh_palette()
        self.info.setText(self._base_info())
        return None

    def _pick_widget(self, i):
        ws_ = [e for k, e in (self.doc or {}).get("entries", []) if k == "widget"]
        if 0 <= i < len(ws_):
            self.cur_w = ws_[i]
            self.ws.set_scripts(self.cur_w["scripts"], arrange=not any(s["x"] or s["y"] for s in
                                                                         self.cur_w["scripts"]))
            self._refresh_palette()

    def _widget_info(self):
        names, methods = [], []
        for sc in (self.cur_w or {}).get("scripts", []):
            bl = sc["blocks"]
            if not bl:
                continue
            if bl[0].kind == "data":
                names += [b.f.get("name") for b in bl[1:] if b.kind in DECL_KINDS and b.f.get("name")]
            elif bl[0].kind == "method":
                methods.append((bl[0].f.get("name") or "f", bl[0].f.get("params", "")))
        return {"names": names, "methods": methods, "layer_fields": self.layer_fields}

    def _refresh_palette(self):
        self.pal.set_groups(palette_blocks(self.role, self._widget_info()))

    def _base_info(self):
        n = sum(len(sc["blocks"]) for sc in (self.cur_w or {}).get("scripts", []))
        return f"блоков: {n}  ·  правый клик — меню, Ctrl+колесо — масштаб"

    def flash(self, text):
        self.info.setText(text)
        self._msg.start(3500)

    def zoom_lbl_update(self):
        self.zoom_btn.setText(f"{round(self.ws.zoom * 100)}%")

    # ── правки ──
    def snapshot(self):
        if self.doc is None:
            return
        self.undo_stack.append(self._dump())
        del self.undo_stack[:-80]
        self.redo_stack.clear()

    def _dump(self):
        i = self.wsel.currentIndex() if self.wsel.count() else 0
        return (copy.deepcopy(self.doc), max(0, i))

    def _restore(self, st):
        self.doc, i = st
        ws_ = [e for k, e in self.doc["entries"] if k == "widget"]
        self.cur_w = ws_[min(i, len(ws_) - 1)]
        self.ws.set_scripts(self.cur_w["scripts"])
        self._emit()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self._dump())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self._dump())
            self._restore(self.redo_stack.pop())

    def _ws_changed(self):
        self.cur_w["scripts"] = self.ws.scripts
        self._emit()
        self._pal_timer.start(400)

    def _emit(self):
        code, self.lmap = gen_doc(self.doc)
        self.last_code = code
        self.ws.error_blk = None
        self.info.setText(self._base_info())
        self.code_changed.emit(code)

    def mark_error_line(self, line: int):
        b = self.lmap.get(line) if line else None
        if b is None and line:                              # ошибка внутри многострочного блока — ближайший выше
            prev = [ln for ln in self.lmap if ln <= line]
            b = self.lmap.get(max(prev)) if prev else None
        if b is not self.ws.error_blk:
            self.ws.error_blk = b
            self.ws.update()
