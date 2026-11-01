# dsl_lib.py
"""
EchoScript — расширенная стандартная библиотека (без Qt и без плеера):

  строки      replace trim starts_with ends_with find substr repeat pad_left pad_right chars
              char code format title_case capitalize count_of is_digit is_alpha
  списки      map filter reduce each find find_index index_of unique shuffle zip flatten concat
              copy deep_copy count any all min_of max_of min_by max_by group_by chunk take drop
              sum avg first last clear swap rotate_list list_of grid_of
  словари     has get_or del_key merge items from_items entries
  типы        type is_num is_str is_list is_dict is_fn is_nil is_widget default
  JSON        to_json from_json
  плавность   ease(name, t) + ease_in/out_* (quad cubic quart quint sine expo circ back elastic bounce)
  столкнов.   point_in_rect point_in_circle rect_hit circle_hit circle_rect_hit line_hit dist dist2
  векторы     angle_to vec_len normalize dot cross rotate_point lerp_angle angle_diff move_toward
  числа       wrap fract mod step pingpong round_to snap fmt_num percent chance rand_gauss
              weighted_choice uid bezier catmull fbm noise2 bit_and bit_or bit_xor bit_not shl shr
  цвета       lighten darken saturate grayscale invert hsl color_rgb brightness contrast_text
              palette_of gradient_at
  время       now clock date timestamp fmt_date elapsed

Методы значений (lst.push(1), s.upper(), d.keys()) — таблицы LIST/STR/DICT_METHODS в dsl_lang.
"""
from __future__ import annotations

import copy as _copy
import json
import math
import random
import time

import dsl_lang as L
from dsl_lang import EchoError

_RND = random.Random()


# ══════════════════════════════════════════════════════════════════════════ #
#  Вызов функций скрипта из Python
# ══════════════════════════════════════════════════════════════════════════ #

def _fn(f, what="функция"):
    if not callable(f):
        raise EchoError(f"ожидалась {what}, а передано значение типа «{L._type_name(f)}»")
    return f


def _call(f, *args):
    """Вызвать функцию скрипта с тем числом аргументов, которое она принимает (fn(v) или fn(v, i))."""
    if f.__class__ is L.UserFn:
        n = len(f.params)
        return L.call_user(f, list(args[:n]) if n < len(args) else list(args), {}, 0)
    if f.__class__ is L.BoundMethod:
        n = len(f.fn.params)
        return L.call_user(f.fn, list(args[:n]) if n < len(args) else list(args), {}, 0, f.inst)
    try:
        return f(*args)
    except TypeError:
        return f(*args[:1])


def _tick():
    L.BUDGET[0] -= 1
    if L.BUDGET[0] < 0:
        raise L._too_long(0)


# ══════════════════════════════════════════════════════════════════════════ #
#  Строки
# ══════════════════════════════════════════════════════════════════════════ #

def _s(v):
    return v if v.__class__ is str else L._to_str(v)


def s_replace(s, a, b="", count=-1):
    return _s(s).replace(_s(a), _s(b), int(count))


def s_trim(s, chars=None):
    return _s(s).strip(chars)


def s_find(s, sub, start=0):
    return _s(s).find(_s(sub), int(start))


def s_substr(s, a, n=None):
    s = _s(s)
    a = int(a)
    return s[a:] if n is None else s[a:a + int(n)]


def s_pad_left(s, n, ch=" "):
    return _s(s).rjust(int(n), (_s(ch) or " ")[0])


def s_pad_right(s, n, ch=" "):
    return _s(s).ljust(int(n), (_s(ch) or " ")[0])


def s_format(template, *args, **kw):
    """format("{0} из {1}", a, b) или format("{name}: {score}", {name: "Игрок", score: 5})."""
    t = _s(template)
    if len(args) == 1 and isinstance(args[0], dict) and not kw:
        kw = args[0]
        args = ()

    def rep(m):
        k = m.group(1)
        if k.isdigit():
            i = int(k)
            return L._to_str(args[i]) if i < len(args) else m.group(0)
        return L._to_str(kw[k]) if k in kw else m.group(0)
    import re
    return re.sub(r"\{(\w+)\}", rep, t)


def s_title(s):
    return " ".join(w[:1].upper() + w[1:] for w in _s(s).split(" "))


def s_cap(s):
    s = _s(s)
    return s[:1].upper() + s[1:]


def s_count(s, sub):
    if isinstance(s, list):
        return sum(1 for x in s if x == sub)
    return _s(s).count(_s(sub))


def s_char(code):
    try:
        return chr(int(code))
    except (ValueError, OverflowError):
        return ""


def s_code(s, i=0):
    s = _s(s)
    return ord(s[int(i)]) if 0 <= int(i) < len(s) else 0


def s_wrap_words(s, n=40):
    """Разбить текст на строки не длиннее n символов (по словам) → список строк."""
    out, cur = [], ""
    for wd in _s(s).split():
        if cur and len(cur) + 1 + len(wd) > int(n):
            out.append(cur)
            cur = wd
        else:
            cur = (cur + " " + wd) if cur else wd
    if cur:
        out.append(cur)
    return out


# ══════════════════════════════════════════════════════════════════════════ #
#  Списки
# ══════════════════════════════════════════════════════════════════════════ #

def _lst(v, fn="функция"):
    if isinstance(v, (list, range, tuple)):
        return v
    if isinstance(v, dict):
        return list(v.values())
    if isinstance(v, str):
        return list(v)
    raise EchoError(f"{fn}: ожидался список, а передано «{L._type_name(v)}»")


def l_map(lst, f):
    f = _fn(f)
    out = []
    for i, v in enumerate(_lst(lst, "map")):
        _tick()
        out.append(_call(f, v, i))
    return out


def l_filter(lst, f):
    f = _fn(f)
    out = []
    for i, v in enumerate(_lst(lst, "filter")):
        _tick()
        if _call(f, v, i):
            out.append(v)
    return out


def l_reduce(lst, f, init=None):
    f = _fn(f)
    acc = init
    first = init is None
    for v in _lst(lst, "reduce"):
        _tick()
        if first:
            acc, first = v, False
            continue
        acc = _call(f, acc, v)
    return acc


def l_each(lst, f):
    f = _fn(f)
    for i, v in enumerate(_lst(lst, "each")):
        _tick()
        _call(f, v, i)
    return None


def l_find(lst, f):
    """Первый элемент, для которого f(элемент) — истина (или равный f, если f — не функция)."""
    for i, v in enumerate(_lst(lst, "find")):
        _tick()
        if (_call(f, v, i) if callable(f) else v == f):
            return v
    return None


def l_find_index(lst, f):
    for i, v in enumerate(_lst(lst, "find_index")):
        _tick()
        if (_call(f, v, i) if callable(f) else v == f):
            return i
    return -1


def l_index_of(lst, v):
    if isinstance(lst, str):
        return lst.find(_s(v))
    try:
        return list(lst).index(v)
    except ValueError:
        return -1


def l_unique(lst):
    out = []
    for v in _lst(lst, "unique"):
        if v not in out:
            out.append(v)
    return out


def l_shuffle(lst):
    out = list(_lst(lst, "shuffle"))
    _RND.shuffle(out)
    return out


def l_zip(*lists):
    return [list(t) for t in zip(*[_lst(x, "zip") for x in lists])]


def l_flatten(lst, depth=1):
    out = []
    for v in _lst(lst, "flatten"):
        if isinstance(v, list) and depth > 0:
            out.extend(l_flatten(v, depth - 1))
        else:
            out.append(v)
    return out


def l_concat(*lists):
    out = []
    for x in lists:
        out.extend(_lst(x, "concat") if not isinstance(x, (int, float, bool)) and x is not None else [x])
    return out


def l_count(lst, f=None):
    if f is None:
        return len(_lst(lst, "count"))
    return sum(1 for i, v in enumerate(_lst(lst, "count")) if (_call(f, v, i) if callable(f) else v == f))


def l_any(lst, f=None):
    return any((_call(f, v, i) if f is not None else v) for i, v in enumerate(_lst(lst, "any")))


def l_all(lst, f=None):
    return all((_call(f, v, i) if f is not None else v) for i, v in enumerate(_lst(lst, "all")))


def l_min_by(lst, f):
    seq = _lst(lst, "min_by")
    return min(seq, key=lambda v: _call(f, v)) if seq else None


def l_max_by(lst, f):
    seq = _lst(lst, "max_by")
    return max(seq, key=lambda v: _call(f, v)) if seq else None


def l_min_of(lst):
    seq = [v for v in _lst(lst, "min_of") if isinstance(v, (int, float))]
    return min(seq) if seq else 0


def l_max_of(lst):
    seq = [v for v in _lst(lst, "max_of") if isinstance(v, (int, float))]
    return max(seq) if seq else 0


def l_group_by(lst, f):
    out = {}
    for v in _lst(lst, "group_by"):
        k = _call(f, v) if callable(f) else (v.get(f) if isinstance(v, dict) else None)
        out.setdefault(L._to_str(k) if not isinstance(k, str) else k, []).append(v)
    return out


def l_chunk(lst, n):
    seq = list(_lst(lst, "chunk"))
    n = max(1, int(n))
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def l_take(lst, n):
    return list(_lst(lst, "take"))[:max(0, int(n))]


def l_drop(lst, n):
    return list(_lst(lst, "drop"))[max(0, int(n)):]


def l_sum(lst, a=0, b=None):
    seq = list(_lst(lst, "sum"))
    seg = seq[int(a):] if b is None else seq[int(a):int(b)]
    return float(sum(v for v in seg if isinstance(v, (int, float))))


def l_avg(lst, a=0, b=None):
    seq = list(_lst(lst, "avg"))
    seg = [v for v in (seq[int(a):] if b is None else seq[int(a):int(b)]) if isinstance(v, (int, float))]
    return float(sum(seg)) / len(seg) if seg else 0.0


def l_clear(lst):
    if isinstance(lst, list):
        lst.clear()
    elif isinstance(lst, dict):
        lst.clear()
    return lst


def l_swap(lst, i, j):
    i, j = int(i), int(j)
    if -len(lst) <= i < len(lst) and -len(lst) <= j < len(lst):
        lst[i], lst[j] = lst[j], lst[i]
    return lst


def l_rotate(lst, k=1):
    seq = list(_lst(lst, "rotate_list"))
    if not seq:
        return seq
    k = int(k) % len(seq)
    return seq[k:] + seq[:k]


def l_list_of(n, f=None):
    """list_of(5) → [0, 1, 2, 3, 4]; list_of(5, fn(i) { … }) — элемент из функции; list_of(3, "x") — ["x", "x", "x"]."""
    n = max(0, min(1_000_000, int(n)))
    if f is None:
        return list(range(n))
    if callable(f):
        out = []
        for i in range(n):
            _tick()
            out.append(_call(f, i))
        return out
    return [_copy.deepcopy(f) for _ in range(n)]


def l_grid_of(cols, rows, v=0):
    """Двумерная сетка rows × cols (список строк): grid[y][x]. v — значение или fn(x, y)."""
    cols, rows = max(0, min(4096, int(cols))), max(0, min(4096, int(rows)))
    if callable(v):
        return [[_call(v, x, y) for x in range(cols)] for y in range(rows)]
    return [[_copy.deepcopy(v) for _ in range(cols)] for _ in range(rows)]


def l_sort(lst, key=None, desc=False):
    seq = list(_lst(lst, "sort"))
    if key is None:
        try:
            return sorted(seq, reverse=bool(desc))
        except TypeError:
            return sorted(seq, key=L._to_str, reverse=bool(desc))
    if isinstance(key, str):
        return sorted(seq, key=lambda x: (x.get(key) if isinstance(x, dict) else x) or 0, reverse=bool(desc))
    return sorted(seq, key=lambda x: _call(key, x), reverse=bool(desc))


def l_join(lst, sep=""):
    return _s(sep).join(L._to_str(x) for x in _lst(lst, "join"))


def l_slice(v, a, b=None):
    return v[int(a):] if b is None else v[int(a):int(b)]


def l_copy(v):
    if isinstance(v, list):
        return list(v)
    if isinstance(v, dict):
        return dict(v)
    return v


def l_deep_copy(v):
    def dc(x):
        if isinstance(x, list):
            return [dc(i) for i in x]
        if isinstance(x, dict):
            return {k: dc(i) for k, i in x.items()}
        return x                                           # виджеты, контуры и т.п. — по ссылке
    return dc(v)


# ══════════════════════════════════════════════════════════════════════════ #
#  Словари и типы
# ══════════════════════════════════════════════════════════════════════════ #

def d_has(d, k):
    return L.contains(d, k)


def d_get_or(d, k, default=None):
    if isinstance(d, dict):
        return d.get(k, default)
    if isinstance(d, list):
        try:
            return d[int(k)]
        except (IndexError, ValueError, TypeError):
            return default
    if isinstance(d, L.Instance):
        return d.fields.get(k, default)
    return default


def d_del(d, k):
    if isinstance(d, dict):
        d.pop(k, None)
    elif isinstance(d, list):
        i = int(k)
        if -len(d) <= i < len(d):
            del d[i]
    return d


def d_merge(*ds):
    out = {}
    for d in ds:
        if isinstance(d, dict):
            out.update(d)
    return out


def d_items(d):
    return [[k, v] for k, v in d.items()] if isinstance(d, dict) else [[i, v] for i, v in enumerate(_lst(d))]


def d_from_items(pairs):
    out = {}
    for p in _lst(pairs, "from_items"):
        if isinstance(p, list) and len(p) >= 2:
            out[p[0] if isinstance(p[0], str) else L._to_str(p[0])] = p[1]
    return out


def type_of(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (list, range)):
        return "list"
    if isinstance(v, dict):
        return "dict"
    if isinstance(v, L.Instance):
        return "widget"
    et = getattr(v, "echo_kind", None)
    if et:
        return et
    if callable(v):
        return "fn"
    return "object"


def to_json(v, pretty=False):
    def clean(x):
        if isinstance(x, (str, int, float, bool)) or x is None:
            return x
        if isinstance(x, (list, range, tuple)):
            return [clean(i) for i in x]
        if isinstance(x, dict):
            return {str(k): clean(i) for k, i in x.items()}
        if x.__class__.__name__ == "QColor":
            return L._to_str(x)
        return None
    return json.dumps(clean(v), ensure_ascii=False, indent=2 if pretty else None)


def from_json(s, default=None):
    try:
        return json.loads(_s(s))
    except (ValueError, TypeError):
        return default


# ══════════════════════════════════════════════════════════════════════════ #
#  Плавность (easing)
# ══════════════════════════════════════════════════════════════════════════ #

def _bounce_out(t):
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def _elastic_out(t):
    if t <= 0 or t >= 1:
        return t
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * (2 * math.pi / 3)) + 1


def _back_out(t):
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


EASE_OUT = {
    "linear": lambda t: t,
    "quad": lambda t: 1 - (1 - t) ** 2,
    "cubic": lambda t: 1 - (1 - t) ** 3,
    "quart": lambda t: 1 - (1 - t) ** 4,
    "quint": lambda t: 1 - (1 - t) ** 5,
    "sine": lambda t: math.sin(t * math.pi / 2),
    "expo": lambda t: 1.0 if t >= 1 else 1 - 2 ** (-10 * t),
    "circ": lambda t: math.sqrt(max(0.0, 1 - (t - 1) ** 2)),
    "back": _back_out,
    "elastic": _elastic_out,
    "bounce": _bounce_out,
}


def ease(name="out_cubic", t=0.0):
    """ease("out_back", t), ease("in_out_sine", t) … t — 0…1. Имена: linear, in_/out_/in_out_ +
    quad cubic quart quint sine expo circ back elastic bounce. Также smooth (= in_out_cubic)."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else float(t)
    n = _s(name).lower().replace("-", "_")
    if n in ("smooth", "smoothstep"):
        return t * t * (3 - 2 * t)
    if n == "linear":
        return t
    mode, _, base = n.partition("_")
    if mode == "in" and base.startswith("out_"):
        mode, base = "in_out", base[4:]
    f = EASE_OUT.get(base)
    if f is None:
        f = EASE_OUT.get(n)
        mode = "out"
        if f is None:
            raise EchoError(f"неизвестная плавность «{name}» (есть: linear, in_/out_/in_out_ + "
                            f"{' '.join(k for k in EASE_OUT if k != 'linear')})")
    if mode == "out":
        return f(t)
    if mode == "in":
        return 1 - f(1 - t)
    if t < 0.5:
        return (1 - f(1 - 2 * t)) / 2
    return (1 + f(2 * t - 1)) / 2


# ══════════════════════════════════════════════════════════════════════════ #
#  Геометрия и столкновения
# ══════════════════════════════════════════════════════════════════════════ #

def point_in_rect(px, py, x, y, w, h):
    return x <= px <= x + w and y <= py <= y + h


def point_in_circle(px, py, cx, cy, r):
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def rect_hit(ax, ay, aw, ah, bx, by, bw, bh):
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def circle_hit(ax, ay, ar, bx, by, br):
    return (ax - bx) ** 2 + (ay - by) ** 2 <= (ar + br) ** 2


def circle_rect_hit(cx, cy, r, x, y, w, h):
    nx = max(x, min(cx, x + w))
    ny = max(y, min(cy, y + h))
    return (cx - nx) ** 2 + (cy - ny) ** 2 <= r * r


def line_hit(x1, y1, x2, y2, x3, y3, x4, y4):
    """Точка пересечения двух отрезков {x, y} или nil."""
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(d) < 1e-12:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / d
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / d
    if 0 <= t <= 1 and 0 <= u <= 1:
        return {"x": x1 + t * (x2 - x1), "y": y1 + t * (y2 - y1)}
    return None


def dist(x1, y1, x2, y2):
    return math.hypot(x2 - x1, y2 - y1)


def dist2(x1, y1, x2, y2):
    return (x2 - x1) ** 2 + (y2 - y1) ** 2


def angle_to(x1, y1, x2, y2):
    """Угол от точки 1 к точке 2 в градусах (0 — вправо, 90 — вниз)."""
    return math.degrees(math.atan2(y2 - y1, x2 - x1))


def vec_len(x, y):
    return math.hypot(x, y)


def normalize(x, y):
    n = math.hypot(x, y)
    return [x / n, y / n] if n > 1e-12 else [0.0, 0.0]


def dot(ax, ay, bx, by):
    return ax * bx + ay * by


def cross(ax, ay, bx, by):
    return ax * by - ay * bx


def rotate_point(x, y, deg, cx=0.0, cy=0.0):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    dx, dy = x - cx, y - cy
    return [cx + dx * c - dy * s, cy + dx * s + dy * c]


def angle_diff(a, b):
    """Кратчайшая разница углов b − a в градусах (−180…180)."""
    return (b - a + 180.0) % 360.0 - 180.0


def lerp_angle(a, b, t):
    return a + angle_diff(a, b) * t


def move_toward(cur, target, step):
    if abs(target - cur) <= step:
        return target
    return cur + step if target > cur else cur - step


# ══════════════════════════════════════════════════════════════════════════ #
#  Числа
# ══════════════════════════════════════════════════════════════════════════ #

def wrap(v, lo, hi):
    span = hi - lo
    return lo if span == 0 else lo + (v - lo) % span


def fract(v):
    return v - math.floor(v)


def mod(a, b):
    return 0.0 if b == 0 else a % b


def step(edge, x):
    return 0.0 if x < edge else 1.0


def pingpong(t, length=1.0):
    if length == 0:
        return 0.0
    t = t % (2 * length)
    return length - abs(t - length)


def round_to(v, digits=0):
    return round(float(v), int(digits))


def snap(v, grid):
    return round(v / grid) * grid if grid else v


def fmt_num(v, digits=0, sep=" "):
    """fmt_num(1234567.8, 1) → «1 234 567.8»."""
    s = f"{float(v):,.{int(digits)}f}"
    return s.replace(",", "\x00").replace("\x00", _s(sep))


def percent(v, digits=0):
    return f"{float(v) * 100:.{int(digits)}f}%"


def chance(p):
    return _RND.random() < float(p)


def rand_gauss(mu=0.0, sigma=1.0):
    return _RND.gauss(float(mu), float(sigma))


def weighted_choice(items, weights):
    items, weights = list(_lst(items)), [max(0.0, float(w)) for w in _lst(weights)]
    if not items or not weights or sum(weights) <= 0:
        return None
    return _RND.choices(items[:len(weights)], weights[:len(items)])[0]


_UID = [0]


def uid(prefix=""):
    _UID[0] += 1
    return f"{_s(prefix)}{_UID[0]}"


def bezier(t, p0, p1, p2, p3):
    u = 1 - t
    return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3


def catmull(t, p0, p1, p2, p3):
    t2, t3 = t * t, t * t * t
    return 0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)


def make_fbm(noise):
    def fbm(x, y=0.0, z=0.0, octaves=4, gain=0.5, lac=2.0):
        a, f, s, norm = 1.0, 1.0, 0.0, 0.0
        for _ in range(max(1, min(8, int(octaves)))):
            s += a * noise(x * f, y * f, z * f)
            norm += a
            a *= gain
            f *= lac
        return s / norm if norm else 0.0
    return fbm


# ══════════════════════════════════════════════════════════════════════════ #
#  Цвета (QColor-совместимо: функции принимают строки/QColor, возвращают QColor)
# ══════════════════════════════════════════════════════════════════════════ #

def make_colors(to_qcolor):
    from PyQt6.QtGui import QColor

    def _clamp(v):
        return 0.0 if v < 0 else 1.0 if v > 1 else v

    def lighten(c, k=0.2):
        q = to_qcolor(c)
        h, s, l, a = q.getHslF()
        return QColor.fromHslF(max(0.0, h), s, _clamp(l + float(k) * (1 - l)), a)

    def darken(c, k=0.2):
        q = to_qcolor(c)
        h, s, l, a = q.getHslF()
        return QColor.fromHslF(max(0.0, h), s, _clamp(l * (1 - float(k))), a)

    def saturate(c, k=0.2):
        q = to_qcolor(c)
        h, s, l, a = q.getHslF()
        return QColor.fromHslF(max(0.0, h), _clamp(s + float(k)), l, a)

    def grayscale(c):
        q = to_qcolor(c)
        g = int(0.299 * q.red() + 0.587 * q.green() + 0.114 * q.blue())
        return QColor(g, g, g, q.alpha())

    def invert(c):
        q = to_qcolor(c)
        return QColor(255 - q.red(), 255 - q.green(), 255 - q.blue(), q.alpha())

    def hsl(h, s=1.0, l=0.5, a=1.0):
        return QColor.fromHslF((float(h) % 360) / 360.0, _clamp(float(s)), _clamp(float(l)), _clamp(float(a)))

    def color_rgb(c):
        """Цвет → {r, g, b, a} (0…255, a — 0…1) и {h, s, v} (h — градусы)."""
        q = to_qcolor(c)
        h, s, v, _a = q.getHsvF()
        return {"r": q.red(), "g": q.green(), "b": q.blue(), "a": q.alphaF(),
                "h": max(0.0, h) * 360.0, "s": s, "v": v}

    def brightness(c):
        q = to_qcolor(c)
        return (0.299 * q.red() + 0.587 * q.green() + 0.114 * q.blue()) / 255.0

    def contrast_text(c, dark="#111111", light="#ffffff"):
        """Цвет текста, читаемый на фоне c (тёмный на светлом, светлый на тёмном)."""
        return to_qcolor(dark) if brightness(c) > 0.6 else to_qcolor(light)

    def gradient_at(colors, t):
        """Цвет в точке t (0…1) градиента из списка цветов."""
        cols = [to_qcolor(x) for x in _lst(colors)]
        if not cols:
            return QColor(0, 0, 0)
        if len(cols) == 1:
            return cols[0]
        t = _clamp(float(t)) * (len(cols) - 1)
        i = min(len(cols) - 2, int(t))
        f = t - i
        a, b = cols[i], cols[i + 1]
        return QColor(int(a.red() + (b.red() - a.red()) * f), int(a.green() + (b.green() - a.green()) * f),
                      int(a.blue() + (b.blue() - a.blue()) * f), int(a.alpha() + (b.alpha() - a.alpha()) * f))

    def palette_of(c, n=5, spread=30):
        """Гармоничная палитра из n цветов вокруг оттенка c."""
        q = to_qcolor(c)
        h, s, v, a = q.getHsvF()
        h = max(0.0, h) * 360
        n = max(1, min(32, int(n)))
        return [QColor.fromHsvF(((h + (i - (n - 1) / 2) * float(spread)) % 360) / 360.0, s, v, a) for i in range(n)]

    return {"lighten": lighten, "darken": darken, "saturate": saturate, "grayscale": grayscale, "invert": invert,
            "hsl": hsl, "color_rgb": color_rgb, "brightness": brightness, "contrast_text": contrast_text,
            "gradient_at": gradient_at, "palette_of": palette_of}


# ══════════════════════════════════════════════════════════════════════════ #
#  Время
# ══════════════════════════════════════════════════════════════════════════ #

_WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]


def now():
    return time.time()


def date(ts=None):
    t = time.localtime(float(ts) if ts is not None else time.time())
    return {"year": t.tm_year, "month": t.tm_mon, "day": t.tm_mday, "hour": t.tm_hour, "minute": t.tm_min,
            "second": t.tm_sec, "weekday": t.tm_wday, "weekday_name": _WEEKDAYS_RU[t.tm_wday],
            "month_name": _MONTHS_RU[t.tm_mon - 1], "yearday": t.tm_yday}


def clock(seconds=False, ts=None):
    t = time.localtime(float(ts) if ts is not None else time.time())
    return time.strftime("%H:%M:%S" if seconds else "%H:%M", t)


def fmt_date(pattern="%d.%m.%Y", ts=None):
    """fmt_date("%d.%m.%Y %H:%M") — как strftime."""
    try:
        return time.strftime(_s(pattern), time.localtime(float(ts) if ts is not None else time.time()))
    except (ValueError, TypeError):
        return ""


# ══════════════════════════════════════════════════════════════════════════ #
#  Сборка
# ══════════════════════════════════════════════════════════════════════════ #

def bit_fns():
    def i(v):
        return int(v)
    return {"bit_and": lambda a, b: i(a) & i(b), "bit_or": lambda a, b: i(a) | i(b),
            "bit_xor": lambda a, b: i(a) ^ i(b), "bit_not": lambda a: ~i(a),
            "shl": lambda a, n: i(a) << max(0, min(62, i(n))), "shr": lambda a, n: i(a) >> max(0, i(n))}


def library(noise, to_qcolor) -> dict:
    g = {
        # строки
        "replace": s_replace, "trim": s_trim, "starts_with": lambda s, p: _s(s).startswith(_s(p)),
        "ends_with": lambda s, p: _s(s).endswith(_s(p)), "find": l_find, "find_str": s_find,
        "substr": s_substr, "repeat": lambda s, n: _s(s) * max(0, min(100000, int(n))),
        "pad_left": s_pad_left, "pad_right": s_pad_right, "chars": lambda s: list(_s(s)), "char": s_char,
        "code": s_code, "format": s_format, "title_case": s_title, "capitalize": s_cap, "count_of": s_count,
        "is_digit": lambda s: _s(s).isdigit(), "is_alpha": lambda s: _s(s).isalpha(), "wrap_words": s_wrap_words,
        # списки
        "map": l_map, "filter": l_filter, "reduce": l_reduce, "each": l_each, "find_index": l_find_index,
        "index_of": l_index_of, "unique": l_unique, "shuffle": l_shuffle, "zip": l_zip, "flatten": l_flatten,
        "concat": l_concat, "copy": l_copy, "deep_copy": l_deep_copy, "count": l_count, "any": l_any,
        "all": l_all, "min_of": l_min_of, "max_of": l_max_of, "min_by": l_min_by, "max_by": l_max_by,
        "group_by": l_group_by, "chunk": l_chunk, "take": l_take, "drop": l_drop, "clear": l_clear,
        "swap": l_swap, "rotate_list": l_rotate, "list_of": l_list_of, "grid_of": l_grid_of,
        "first": lambda v: (v[0] if v else None), "last": lambda v: (v[-1] if v else None),
        # словари / типы / JSON
        "has": d_has, "get_or": d_get_or, "del_key": d_del, "merge": d_merge, "items": d_items,
        "from_items": d_from_items, "type": type_of, "is_num": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "is_str": lambda v: isinstance(v, str), "is_list": lambda v: isinstance(v, list),
        "is_dict": lambda v: isinstance(v, dict), "is_fn": lambda v: callable(v) and not isinstance(v, L.Instance),
        "is_nil": lambda v: v is None, "is_widget": lambda v: isinstance(v, L.Instance),
        "default": lambda v, d: d if v is None else v, "to_json": to_json, "from_json": from_json,
        # плавность
        "ease": ease,
        # геометрия
        "point_in_rect": point_in_rect, "point_in_circle": point_in_circle, "rect_hit": rect_hit,
        "circle_hit": circle_hit, "circle_rect_hit": circle_rect_hit, "line_hit": line_hit, "dist": dist,
        "dist2": dist2, "angle_to": angle_to, "vec_len": vec_len, "normalize": normalize, "dot": dot,
        "cross": cross, "rotate_point": rotate_point, "angle_diff": angle_diff, "lerp_angle": lerp_angle,
        "move_toward": move_toward,
        # числа
        "wrap": wrap, "fract": fract, "mod": mod, "step": step, "pingpong": pingpong, "round_to": round_to,
        "snap": snap, "fmt_num": fmt_num, "percent": percent, "chance": chance, "rand_gauss": rand_gauss,
        "weighted_choice": weighted_choice, "uid": uid, "bezier": bezier, "catmull": catmull,
        "fbm": make_fbm(noise), "noise2": lambda x, y: noise(x, y, 0.0), "trunc": lambda v: float(math.trunc(v)),
        "log2": lambda v: math.log2(max(1e-12, v)), "cbrt": lambda v: math.copysign(abs(v) ** (1 / 3), v),
        "is_nan": lambda v: isinstance(v, float) and math.isnan(v), "INF": math.inf,
        # время
        "now": now, "date": date, "clock": clock, "fmt_date": fmt_date,
    }
    g.update(bit_fns())
    g.update(make_colors(to_qcolor))
    return g


def install_methods(std: dict):
    """Методы значений: список.метод(…), строка.метод(…), словарь.метод(…)."""
    lm = {"push": std["push"], "pop": std["pop"], "insert": std["insert"], "remove": std["remove"],
          "map": l_map, "filter": l_filter, "reduce": l_reduce, "each": l_each, "find": l_find,
          "find_index": l_find_index, "index_of": l_index_of, "contains": lambda l, v: v in l,
          "sort": l_sort, "reverse": lambda l: list(reversed(l)), "join": l_join, "slice": l_slice,
          "unique": l_unique, "shuffle": l_shuffle, "copy": l_copy, "clear": l_clear, "sum": l_sum,
          "avg": l_avg, "min": l_min_of, "max": l_max_of, "count": l_count, "any": l_any, "all": l_all,
          "take": l_take, "drop": l_drop, "chunk": l_chunk, "flatten": l_flatten, "swap": l_swap,
          "random": lambda l: _RND.choice(l) if l else None, "is_empty": lambda l: len(l) == 0,
          "concat": lambda l, *o: l_concat(l, *o), "group_by": l_group_by, "min_by": l_min_by,
          "max_by": l_max_by}
    sm = {"upper": lambda s: s.upper(), "lower": lambda s: s.lower(), "trim": s_trim, "split":
          lambda s, sep=" ": s.split(sep) if sep != "" else list(s), "replace": s_replace,
          "starts_with": lambda s, p: s.startswith(_s(p)), "ends_with": lambda s, p: s.endswith(_s(p)),
          "contains": lambda s, p: _s(p) in s, "find": s_find, "slice": l_slice, "substr": s_substr,
          "repeat": lambda s, n: s * max(0, min(100000, int(n))), "pad_left": s_pad_left,
          "pad_right": s_pad_right, "chars": lambda s: list(s), "format": s_format, "count": s_count,
          "capitalize": s_cap, "title": s_title, "is_empty": lambda s: s == "", "num": lambda s: _num(s),
          "lines": lambda s: s.splitlines(), "words": lambda s: s.split(), "reverse": lambda s: s[::-1]}
    dm = {"keys": lambda d: list(d.keys()), "values": lambda d: list(d.values()), "items": d_items,
          "has": lambda d, k: k in d, "get": lambda d, k, default=None: d.get(k, default), "remove": d_del,
          "merge": lambda d, *o: d_merge(d, *o), "copy": lambda d: dict(d), "clear": l_clear,
          "to_json": to_json, "is_empty": lambda d: len(d) == 0}
    L.LIST_METHODS.clear()
    L.LIST_METHODS.update(lm)
    L.STR_METHODS.clear()
    L.STR_METHODS.update(sm)
    L.DICT_METHODS.clear()
    L.DICT_METHODS.update(dm)


def _num(s, default=0.0):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default
