# dsl_lang.py
"""
EchoScript — язык тем ECHOES: лексер, парсер и компилятор.

Цепочка:  исходный текст → токены (Lexer) → дерево разбора (Parser, кортежи-узлы)
          → компиляция в замыкания Python (Compiler) → Program.

Почему замыкания, а не обход дерева на каждом кадре: каждый узел заранее
превращается в маленькую функцию f(frame); при исполнении не нужно смотреть на
тип узла, искать оператор и т.п. Это в разы быстрее «наивного» интерпретатора
и позволяет рисовать визуализаторы на 60 кадрах в секунду.

Язык безопасен: из скрипта нельзя добраться до Python (нет import/eval/доступа к
атрибутам объектов), есть только то, что явно положено в глобальное окружение
(рантайм — dsl_runtime.py). Бесконечный цикл не повесит плеер: у каждого вызова
обработчика есть «бюджет» шагов.

Синтаксис — кратко (полная справка — dsl_docs.py):

    // комментарий            /* многострочный */
    let x = 10                 переменная (точка с запятой не обязательна)
    x += 1                     = += -= *= /=
    fn f(a, b = 2) { return a + b }
    if a > 1 { } elif a < 0 { } else { }
    while cond { }             for i in 0..10 { }   for i, v in list { }
    break  continue  return
    [1, 2, 3]   {name: "x", "key": 1}   cond ? a : b   a and b or not c
    widget Name { prop p = 1   state s = 0   fn m() { }   on draw { } }
    theme { name: "Loom", bg: "#000" }
    scene { add Name { x: 0, y: 0, w: 1, h: 1 } }
"""
from __future__ import annotations

import math

# ══════════════════════════════════════════════════════════════════════════ #
#  Ошибки
# ══════════════════════════════════════════════════════════════════════════ #


class EchoError(Exception):
    """Ошибка скрипта: сообщение по-русски + строка/столбец."""

    fatal = False                     # бюджет шагов исчерпан — try/catch такую ошибку не ловит

    def __init__(self, msg, line=0, col=0):
        super().__init__(msg)
        self.msg, self.line, self.col = msg, line, col

    def __str__(self):
        return f"строка {self.line}: {self.msg}" if self.line else self.msg


# ══════════════════════════════════════════════════════════════════════════ #
#  Лексер
# ══════════════════════════════════════════════════════════════════════════ #

KEYWORDS = {"let", "fn", "return", "if", "elif", "else", "while", "for", "in", "break", "continue",
            "and", "or", "not", "true", "false", "nil", "widget", "on", "prop", "state", "scene", "add",
            "theme", "self"}
# «мягкие» ключевые слова: работают только на своём месте (match x { … }, try { … } catch e { … },
# child имя = Виджет { … } в виджете), а в остальном коде остаются обычными именами — старые скрипты
# с переменными match / child / try не ломаются
SOFT_KEYWORDS = {"child", "match", "try", "catch"}
OPS3 = ()
OPS2 = ("**", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "..", "=>")
OPS1 = "+-*/%<>=()[]{},.:;?"


class Tok:
    __slots__ = ("kind", "val", "line", "col")

    def __init__(self, kind, val, line, col):
        self.kind, self.val, self.line, self.col = kind, val, line, col

    def __repr__(self):
        return f"{self.kind}:{self.val!r}@{self.line}"


def tokenize(src: str, line0: int = 1) -> list[Tok]:
    toks: list[Tok] = []
    i, n, line, col = 0, len(src), line0, 1
    depth = 0                                     # внутри () и [] переводы строк не важны

    def add(kind, val, l, c):
        toks.append(Tok(kind, val, l, c))

    while i < n:
        ch = src[i]
        if ch == "\n":
            if depth == 0 and toks and toks[-1].kind != "NL":
                add("NL", "\n", line, col)
            i += 1
            line += 1
            col = 1
            continue
        if ch in " \t\r":
            i += 1
            col += 1
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise EchoError("незакрытый комментарий /* … */", line, col)
            seg = src[i:j + 2]
            line += seg.count("\n")
            col = 1 if "\n" in seg else col + len(seg)
            i = j + 2
            continue
        l0, c0 = line, col
        if ch.isdigit() or (ch == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            while j < n and (src[j].isdigit() or src[j] == "_"):
                j += 1
            if j < n and src[j] == "." and not src.startswith("..", j):
                j += 1
                while j < n and src[j].isdigit():
                    j += 1
            if j < n and src[j] in "eE" and j + 1 < n and (src[j + 1].isdigit() or src[j + 1] in "+-"):
                j += 2
                while j < n and src[j].isdigit():
                    j += 1
            text = src[i:j].replace("_", "")
            try:
                val = float(text) if any(c in text for c in ".eE") else int(text)
            except ValueError:
                raise EchoError(f"странное число «{text}»", l0, c0)
            add("NUM", val, l0, c0)
            col += j - i
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            add("KW" if word in KEYWORDS else "ID", word, l0, c0)
            col += j - i
            i = j
            continue
        if ch in "\"'":
            q = ch
            j = i + 1
            out = []
            while j < n and src[j] != q:
                c = src[j]
                if c == "\n":
                    raise EchoError("строка не закрыта кавычкой", l0, c0)
                if c == "\\" and j + 1 < n:
                    e = src[j + 1]
                    out.append({"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}.get(e, "\\" + e))
                    j += 2
                    continue
                out.append(c)
                j += 1
            if j >= n:
                raise EchoError("строка не закрыта кавычкой", l0, c0)
            add("STR", "".join(out), l0, c0)
            col += j + 1 - i
            i = j + 1
            continue
        if ch == "`":
            # шаблонная строка: `Счёт: {score}, жизней: {lives}` — можно в несколько строк
            j = i + 1
            parts, buf = [], []
            ln = line
            while j < n and src[j] != "`":
                c = src[j]
                if c == "\\" and j + 1 < n:
                    e = src[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", "`": "`", "{": "{", "}": "}"}.get(e, "\\" + e))
                    j += 2
                    continue
                if c == "{":
                    k, d = j + 1, 1
                    while k < n and d:
                        if src[k] == "{":
                            d += 1
                        elif src[k] == "}":
                            d -= 1
                        k += 1
                    if d:
                        raise EchoError("в шаблонной строке не закрыта «{»", ln, c0)
                    if buf:
                        parts.append((False, "".join(buf), ln))
                        buf = []
                    parts.append((True, src[j + 1:k - 1], ln))
                    ln += src.count("\n", j, k)
                    j = k
                    continue
                if c == "\n":
                    ln += 1
                buf.append(c)
                j += 1
            if j >= n:
                raise EchoError("шаблонная строка не закрыта обратной кавычкой `", l0, c0)
            if buf or not parts:
                parts.append((False, "".join(buf), ln))
            add("TPL", parts, l0, c0)
            seg = src[i:j + 1]
            line += seg.count("\n")
            col = (len(seg) - seg.rfind("\n")) if "\n" in seg else col + len(seg)
            i = j + 1
            continue
        two = src[i:i + 2]
        if two in OPS2:
            add("OP", two, l0, c0)
            i += 2
            col += 2
            continue
        if ch in OPS1:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(0, depth - 1)
            add("OP", ch, l0, c0)
            i += 1
            col += 1
            continue
        raise EchoError(f"непонятный символ «{ch}»", l0, c0)
    add("NL", "\n", line, col)
    add("EOF", None, line, col)
    return toks


# ══════════════════════════════════════════════════════════════════════════ #
#  Парсер → узлы-кортежи (тип, строка, ...)
# ══════════════════════════════════════════════════════════════════════════ #


class Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    # ── помощники ──
    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def is_op(self, v, k=0):
        tok = self.peek(k)
        return tok.kind == "OP" and tok.val == v

    def is_kw(self, v, k=0):
        tok = self.peek(k)
        return tok.kind == "KW" and tok.val == v

    def expect_op(self, v):
        tok = self.next()
        if tok.kind != "OP" or tok.val != v:
            raise EchoError(f"ожидалось «{v}», а встретилось {self._show(tok)}", tok.line, tok.col)
        return tok

    def expect_id(self, what="имя"):
        tok = self.next()
        if tok.kind != "ID":
            raise EchoError(f"ожидалось {what}, а встретилось {self._show(tok)}", tok.line, tok.col)
        return tok

    @staticmethod
    def _show(tok):
        if tok.kind == "EOF":
            return "конец файла"
        if tok.kind == "NL":
            return "конец строки"
        return f"«{tok.val}»"

    def skip_nl(self):
        while self.peek().kind == "NL" or self.is_op(";"):
            self.i += 1

    def end_stmt(self):
        tok = self.peek()
        if tok.kind in ("NL", "EOF") or (tok.kind == "OP" and tok.val in (";", "}")):
            if tok.kind == "NL" or (tok.kind == "OP" and tok.val == ";"):
                self.i += 1
            return
        raise EchoError(f"лишнее в конце инструкции: {self._show(tok)}", tok.line, tok.col)

    # ── программа ──
    def program(self):
        body = []
        self.skip_nl()
        while self.peek().kind != "EOF":
            body.append(self.top_stmt())
            self.skip_nl()
        return body

    def top_stmt(self):
        if self.is_kw("widget"):
            return self.widget_decl()
        if self.is_kw("theme"):
            tok = self.next()
            m = self.map_lit()
            self.end_stmt()
            return ("theme", tok.line, m)
        if self.is_kw("scene"):
            tok = self.next()
            return ("scene", tok.line, self.block())
        return self.stmt()

    def widget_decl(self):
        tok = self.next()
        name = self.expect_id("имя виджета").val
        self.skip_nl()
        self.expect_op("{")
        props, states, methods, handlers = [], [], [], []
        self.skip_nl()
        while not self.is_op("}"):
            t = self.peek()
            if self.is_kw("prop") or self.is_kw("state"):
                kind = self.next().val
                nm = self.expect_id().val
                self.expect_op("=")
                ex = self.expr()
                (props if kind == "prop" else states).append((nm, ex, t.line))
                self.end_stmt()
            elif t.kind == "ID" and t.val == "child" and self.peek(1).kind == "ID":
                # child имя = Виджет { свойства }  →  state имя = spawn("Виджет", { свойства })
                self.next()
                nm = self.expect_id("имя вложенного виджета").val
                self.expect_op("=")
                ct = self.next()
                if ct.kind not in ("ID", "STR"):
                    raise EchoError("после «child имя =» ожидалось имя виджета (свой класс или блок каталога)",
                                    ct.line, ct.col)
                m = self.map_lit() if self.is_op("{") else ("map", ct.line, [])
                ex = ("call", t.line, ("name", t.line, "spawn"), [("str", ct.line, ct.val), m], [])
                states.append((nm, ex, t.line))
                self.end_stmt()
            elif self.is_kw("fn"):
                self.next()
                nm = self.expect_id("имя функции").val
                params = self.params()
                methods.append((nm, params, self.block(), t.line))
            elif self.is_kw("on"):
                self.next()
                ev = self.expect_id("имя события").val
                params = self.params() if self.is_op("(") else []
                handlers.append((ev, params, self.block(), t.line))
            else:
                raise EchoError(f"в виджете ожидалось prop / state / child / fn / on, а встретилось {self._show(t)}",
                                t.line, t.col)
            self.skip_nl()
        self.expect_op("}")
        return ("widget", tok.line, name, props, states, methods, handlers)

    def params(self):
        self.expect_op("(")
        out = []
        while not self.is_op(")"):
            nm = self.expect_id("имя параметра").val
            default = None
            if self.is_op("="):
                self.next()
                default = self.expr()
            out.append((nm, default))
            if not self.is_op(")"):
                self.expect_op(",")
        self.expect_op(")")
        return out

    def block(self):
        self.skip_nl_only()
        self.expect_op("{")
        body = []
        self.skip_nl()
        while not self.is_op("}"):
            if self.peek().kind == "EOF":
                t = self.peek()
                raise EchoError("блок не закрыт — не хватает «}»", t.line, t.col)
            body.append(self.stmt())
            self.skip_nl()
        self.expect_op("}")
        return body

    def skip_nl_only(self):
        while self.peek().kind == "NL":
            self.i += 1

    # ── инструкции ──
    def stmt(self):
        t = self.peek()
        if t.kind == "KW":
            v = t.val
            if v == "let":
                self.next()
                nm = self.expect_id().val
                ex = None
                if self.is_op("="):
                    self.next()
                    ex = self.expr()
                self.end_stmt()
                return ("let", t.line, nm, ex)
            if v == "fn" and self.peek(1).kind == "ID":
                self.next()
                nm = self.next().val
                params = self.params()
                return ("fndef", t.line, nm, params, self.block())
            if v == "if":
                self.next()
                arms = [(self.expr(), self.block())]
                els = None
                while True:
                    k = self.i
                    self.skip_nl_only()
                    if self.is_kw("elif"):
                        self.next()
                        arms.append((self.expr(), self.block()))
                    elif self.is_kw("else"):
                        self.next()
                        if self.is_kw("if"):
                            sub = self.stmt()
                            els = [sub]
                        else:
                            els = self.block()
                        break
                    else:
                        self.i = k
                        break
                return ("if", t.line, arms, els)
            if v == "while":
                self.next()
                cond = self.expr()
                return ("while", t.line, cond, self.block())
            if v == "for":
                self.next()
                a = self.expect_id("имя переменной цикла").val
                b = None
                if self.is_op(","):
                    self.next()
                    b = self.expect_id("имя переменной цикла").val
                if not self.is_kw("in"):
                    tok = self.peek()
                    raise EchoError("в цикле for ожидалось «in»", tok.line, tok.col)
                self.next()
                it = self.expr()
                return ("for", t.line, a, b, it, self.block())
            if v == "return":
                self.next()
                ex = None
                if not (self.peek().kind in ("NL", "EOF") or self.is_op(";") or self.is_op("}")):
                    ex = self.expr()
                self.end_stmt()
                return ("return", t.line, ex)
            if v in ("break", "continue"):
                self.next()
                self.end_stmt()
                return (v, t.line)
            if v == "add":
                self.next()
                nm = self.expect_id("имя виджета").val
                m = self.map_lit() if self.is_op("{") else ("map", t.line, [])
                self.end_stmt()
                return ("add", t.line, nm, m)
        if t.kind == "ID" and t.val == "try" and self.is_op("{", 1):
            self.next()
            body = self.block()
            k = self.i
            self.skip_nl_only()
            name, handler = None, []
            nt = self.peek()
            if nt.kind == "ID" and nt.val == "catch":
                self.next()
                if self.peek().kind == "ID":
                    name = self.next().val
                handler = self.block()
            else:
                self.i = k
            return ("try", t.line, body, name, handler)
        if t.kind == "ID" and t.val == "match" and not (self.peek(1).kind == "OP" and
                                                         self.peek(1).val in ("=", "+=", "-=", "*=", "/=", "%=",
                                                                              "(", ".", "[", ")", ",")):
            return self.match_stmt()
        if t.kind == "OP" and t.val == "{":
            return ("block", t.line, self.block())
        ex = self.expr()
        if self.peek().kind == "OP" and self.peek().val in ("=", "+=", "-=", "*=", "/=", "%="):
            op = self.next().val
            if ex[0] not in ("name", "index", "member"):
                raise EchoError("присваивать можно только переменной, элементу списка или полю", t.line, t.col)
            val = self.expr()
            self.end_stmt()
            return ("assign", t.line, op, ex, val)
        self.end_stmt()
        return ("expr", t.line, ex)

    def match_stmt(self):
        """match значение { 1, 2 => … ; "a" => { … } ; 0..10 => … ; else => … }"""
        t = self.next()
        subj = self.expr()
        self.skip_nl_only()
        self.expect_op("{")
        arms, default = [], None
        self.skip_nl()
        while not self.is_op("}"):
            if self.peek().kind == "EOF":
                tt = self.peek()
                raise EchoError("match не закрыт — не хватает «}»", tt.line, tt.col)
            if self.is_kw("else"):
                self.next()
                self.expect_op("=>")
                default = self._arm_body()
            else:
                pats = [self.expr()]
                while self.is_op(","):
                    self.next()
                    pats.append(self.expr())
                self.expect_op("=>")
                arms.append((pats, self._arm_body()))
            self.skip_nl()
        self.expect_op("}")
        return ("match", t.line, subj, arms, default)

    def _arm_body(self):
        self.skip_nl_only()
        if self.is_op("{"):
            return self.block()
        st = self.stmt()
        return [st]

    # ── выражения (от низкого приоритета к высокому) ──
    def expr(self):
        cond = self.or_()
        if self.is_op("?"):
            t = self.next()
            a = self.expr()
            self.expect_op(":")
            b = self.expr()
            return ("tern", t.line, cond, a, b)
        return cond

    def or_(self):
        left = self.and_()
        while self.is_kw("or"):
            t = self.next()
            left = ("or", t.line, left, self.and_())
        return left

    def and_(self):
        left = self.not_()
        while self.is_kw("and"):
            t = self.next()
            left = ("and", t.line, left, self.not_())
        return left

    def not_(self):
        if self.is_kw("not"):
            t = self.next()
            return ("not", t.line, self.not_())
        return self.cmp()

    def cmp(self):
        left = self.range_()
        while True:
            if self.peek().kind == "OP" and self.peek().val in ("==", "!=", "<", "<=", ">", ">="):
                t = self.next()
                left = ("bin", t.line, t.val, left, self.range_())
            elif self.is_kw("in"):                     # x in список / строка / словарь (ключ) / диапазон
                t = self.next()
                left = ("in", t.line, left, self.range_(), False)
            elif self.is_kw("not") and self.is_kw("in", 1):
                t = self.next()
                self.next()
                left = ("in", t.line, left, self.range_(), True)
            else:
                return left

    def range_(self):
        left = self.add()
        if self.is_op(".."):
            t = self.next()
            return ("range", t.line, left, self.add())
        return left

    def add(self):
        left = self.mul()
        while self.peek().kind == "OP" and self.peek().val in ("+", "-"):
            t = self.next()
            left = ("bin", t.line, t.val, left, self.mul())
        return left

    def mul(self):
        left = self.unary()
        while self.peek().kind == "OP" and self.peek().val in ("*", "/", "%"):
            t = self.next()
            left = ("bin", t.line, t.val, left, self.unary())
        return left

    def unary(self):
        if self.is_op("-"):
            t = self.next()
            return ("neg", t.line, self.unary())
        if self.is_op("+"):
            self.next()
            return self.unary()
        return self.power()

    def power(self):
        base = self.postfix()
        if self.is_op("**"):
            t = self.next()
            return ("bin", t.line, "**", base, self.unary())
        return base

    def postfix(self):
        node = self.primary()
        while True:
            if self.is_op("("):
                t = self.next()
                args, kwargs = [], []
                while not self.is_op(")"):
                    if self.peek().kind == "ID" and self.is_op(":", 1):
                        k = self.next().val
                        self.next()
                        kwargs.append((k, self.expr()))
                    else:
                        if kwargs:
                            tt = self.peek()
                            raise EchoError("обычный аргумент после именованного", tt.line, tt.col)
                        args.append(self.expr())
                    if not self.is_op(")"):
                        self.expect_op(",")
                self.expect_op(")")
                node = ("call", t.line, node, args, kwargs)
            elif self.is_op("["):
                t = self.next()
                idx = self.expr()
                self.expect_op("]")
                node = ("index", t.line, node, idx)
            elif self.is_op("."):
                t = self.next()
                nm = self.next()
                if nm.kind not in ("ID", "KW"):
                    raise EchoError("после точки ожидалось имя поля", nm.line, nm.col)
                node = ("member", t.line, node, nm.val)
            else:
                return node

    def primary(self):
        t = self.next()
        if t.kind == "NUM":
            return ("num", t.line, t.val)
        if t.kind == "STR":
            return ("str", t.line, t.val)
        if t.kind == "TPL":
            parts = []
            for is_expr, text, ln in t.val:
                if not is_expr:
                    parts.append(("str", ln, text))
                    continue
                sub = Parser(tokenize(text, ln))
                sub.skip_nl()
                e = sub.expr()
                sub.skip_nl()
                if sub.peek().kind != "EOF":
                    tt = sub.peek()
                    raise EchoError(f"в шаблонной строке лишнее: {sub._show(tt)}", tt.line, tt.col)
                parts.append(e)
            return ("tpl", t.line, parts)
        if t.kind == "ID":
            return ("name", t.line, t.val)
        if t.kind == "KW":
            if t.val == "true":
                return ("const", t.line, True)
            if t.val == "false":
                return ("const", t.line, False)
            if t.val == "nil":
                return ("const", t.line, None)
            if t.val == "self":
                return ("self", t.line)
            if t.val == "theme":                    # в выражениях theme — словарь текущей темы (theme.accent …)
                return ("name", t.line, "theme")
            if t.val == "fn":
                params = self.params()
                return ("fnlit", t.line, params, self.block())
        if t.kind == "OP":
            if t.val == "(":
                e = self.expr()
                self.expect_op(")")
                return e
            if t.val == "[":
                items = []
                while not self.is_op("]"):
                    items.append(self.expr())
                    if not self.is_op("]"):
                        self.expect_op(",")
                self.expect_op("]")
                return ("list", t.line, items)
            if t.val == "{":
                self.i -= 1
                return self.map_lit()
        raise EchoError(f"ожидалось выражение, а встретилось {self._show(t)}", t.line, t.col)

    def map_lit(self):
        t = self.expect_op("{")
        items = []
        self.skip_nl()
        while not self.is_op("}"):
            k = self.next()
            if k.kind not in ("ID", "STR", "KW"):
                raise EchoError("ключ словаря — имя или строка", k.line, k.col)
            self.expect_op(":")
            items.append((k.val, self.expr()))
            self.skip_nl()
            if not self.is_op("}"):
                if self.is_op(","):
                    self.next()
                self.skip_nl()
        self.expect_op("}")
        return ("map", t.line, items)


def parse(src: str):
    return Parser(tokenize(src)).program()


# ══════════════════════════════════════════════════════════════════════════ #
#  Исполнение: кадры, функции, виджеты
# ══════════════════════════════════════════════════════════════════════════ #

BUDGET = [0]                       # шаги, оставшиеся у текущего вызова обработчика
DEFAULT_BUDGET = 3_000_000


def _too_long(line):
    e = EchoError("скрипт выполняется слишком долго (бесконечный цикл?)", line)
    e.fatal = True
    return e


def tick_budget(line):
    BUDGET[0] -= 1
    if BUDGET[0] < 0:
        raise _too_long(line)


class Frame:
    __slots__ = ("vars", "parent", "inst", "g")

    def __init__(self, vars_, parent, inst, g):
        self.vars, self.parent, self.inst, self.g = vars_, parent, inst, g


class _Ret:
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v


_BRK = object()
_CONT = object()


class UserFn:
    """Функция, объявленная в скрипте (замыкание над кадром, где объявлена)."""
    __slots__ = ("name", "params", "defaults", "body", "closure", "inst")

    def __init__(self, name, params, defaults, body, closure, inst=None):
        self.name, self.params, self.defaults, self.body, self.closure, self.inst = \
            name, params, defaults, body, closure, inst

    def __call__(self, *args, **kwargs):
        return call_user(self, list(args), kwargs, 0)

    def __repr__(self):
        return f"<fn {self.name}>"


def call_user(fn: UserFn, args, kwargs, line, inst=None):
    p = fn.params
    if len(args) > len(p):
        raise EchoError(f"функция {fn.name}() принимает {len(p)} аргумент(а), передано {len(args)}", line)
    fr = Frame({}, fn.closure, inst if inst is not None else fn.inst, fn.closure.g if fn.closure else None)
    v = fr.vars
    for i, name in enumerate(p):
        if i < len(args):
            v[name] = args[i]
        elif name in kwargs:
            v[name] = kwargs[name]
        elif fn.defaults[i] is not None:
            v[name] = fn.defaults[i](fr)
        else:
            v[name] = None
    for k in kwargs:
        if k not in p:
            raise EchoError(f"у функции {fn.name}() нет параметра «{k}»", line)
    res = fn.body(fr)
    if res.__class__ is _Ret:
        return res.v
    return None


class WidgetClass:
    def __init__(self, name, props, states, methods, handlers, line):
        self.name, self.props, self.states, self.methods, self.handlers, self.line = \
            name, props, states, methods, handlers, line


class Instance:
    """Экземпляр виджета: поля (prop/state + геометрия), ссылки на класс, вложенные виджеты (kids)
    и родителя (parent) — виджет может жить внутри другого виджета."""
    __slots__ = ("cls", "fields", "error", "uid", "line", "kids", "parent", "scene", "dead", "psize")

    def __init__(self, cls, uid, line=0):
        self.cls, self.fields, self.error, self.uid, self.line = cls, {}, None, uid, line
        self.kids: list = []
        self.parent = None
        self.scene = None
        self.dead = False
        self.psize = None                      # размер родителя, под который пересчитаны доли x/y/w/h

    def __repr__(self):
        return f"<{self.cls.name}>"


class BoundMethod:
    __slots__ = ("inst", "fn")

    def __init__(self, inst, fn):
        self.inst, self.fn = inst, fn

    def __call__(self, *args, **kwargs):
        return call_user(self.fn, list(args), kwargs, 0, self.inst)


class InstFn:
    """Встроенная функция, которой нужен вызывающий виджет (spawn, emit, after…): компилятор
    подставляет его первым аргументом. Из Python (map/filter) вызывается без виджета."""
    __slots__ = ("fn", "name")

    def __init__(self, fn, name=""):
        self.fn, self.name = fn, name or getattr(fn, "__name__", "fn")

    def __call__(self, *args, **kwargs):
        return self.fn(None, *args, **kwargs)

    def __repr__(self):
        return f"<fn {self.name}>"


# методы значений: список.push(4), строка.upper(), словарь.keys() — таблицы заполняет рантайм
LIST_METHODS: dict = {}
STR_METHODS: dict = {}
DICT_METHODS: dict = {}


class _Bound:
    """Метод значения (lst.push) — функция с уже подставленным первым аргументом."""
    __slots__ = ("fn", "obj")

    def __init__(self, fn, obj):
        self.fn, self.obj = fn, obj

    def __call__(self, *args, **kwargs):
        return self.fn(self.obj, *args, **kwargs)


# ══════════════════════════════════════════════════════════════════════════ #
#  Компилятор: узел → замыкание
# ══════════════════════════════════════════════════════════════════════════ #

def _type_name(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "логическое"
    if isinstance(v, (int, float)):
        return "число"
    if isinstance(v, str):
        return "строка"
    if isinstance(v, list):
        return "список"
    if isinstance(v, dict):
        return "словарь"
    if isinstance(v, Instance):
        return "виджет"
    et = getattr(v, "echo_type", None)
    if et:
        return et
    if callable(v):
        return "функция"
    return type(v).__name__


def _add(a, b):
    if a.__class__ is str or b.__class__ is str:
        return f"{_to_str(a)}{_to_str(b)}"
    return a + b


def _to_str(v):
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, list):
        return "[" + ", ".join(_to_str(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_to_str(x)}" for k, x in v.items()) + "}"
    if v.__class__.__name__ == "QColor":
        return "#%02x%02x%02x%02x" % (v.red(), v.green(), v.blue(), v.alpha())
    return str(v)


def _div(a, b):
    if b == 0:
        return 0.0                              # в визуализаторах деление на 0 — частый и безобидный случай
    return a / b


def _mod(a, b):
    if b == 0:
        return 0.0
    return a % b


BINOPS = {
    "+": _add,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": _div,
    "%": _mod,
    "**": lambda a, b: a ** b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _mk_fast(pyop):
    """Фабрика специализированных замыканий для арифметики/сравнений (основная нагрузка визуализаторов)."""
    def build(a, b, c, line, sym, left_const=False):
        def err(x, y):
            return EchoError(f"операция «{sym}» не подходит для «{_type_name(x)}» и «{_type_name(y)}»", line)
        if b is None:                               # x OP константа
            def f(fr):
                x = a(fr)
                try:
                    return pyop(x, c)
                except TypeError:
                    raise err(x, c)
            return f
        if a is None:                               # константа OP y
            def g(fr):
                y = b(fr)
                try:
                    return pyop(c, y)
                except TypeError:
                    raise err(c, y)
            return g

        def h(fr):
            x = a(fr)
            y = b(fr)
            try:
                return pyop(x, y)
            except TypeError:
                raise err(x, y)
        return h
    return build


import operator as _op

_FAST = {"-": _mk_fast(_op.sub), "*": _mk_fast(_op.mul), "<": _mk_fast(_op.lt), "<=": _mk_fast(_op.le),
         ">": _mk_fast(_op.gt), ">=": _mk_fast(_op.ge), "==": _mk_fast(_op.eq), "!=": _mk_fast(_op.ne),
         "+": _mk_fast(_add), "/": _mk_fast(_div), "%": _mk_fast(_mod)}


def get_member(obj, name, line):
    cls = obj.__class__
    if cls is dict:
        if name in obj:
            return obj[name]
        m = DICT_METHODS.get(name)
        return _Bound(m, obj) if m is not None else None
    if cls is Instance:
        f = obj.fields
        if name in f:
            return f[name]
        m = obj.cls.methods.get(name)
        if m is not None:
            return BoundMethod(obj, m)
        if name == "parent":
            return obj.parent
        if name in ("children", "kids"):
            return list(obj.kids)
        if name == "class":
            return obj.cls.name
        return None
    if cls is list:
        if name in ("length", "len", "size"):
            return len(obj)
        if name == "first":
            return obj[0] if obj else None
        if name == "last":
            return obj[-1] if obj else None
        m = LIST_METHODS.get(name)
        if m is not None:
            return _Bound(m, obj)
    elif cls is str:
        if name in ("length", "len", "size"):
            return len(obj)
        m = STR_METHODS.get(name)
        if m is not None:
            return _Bound(m, obj)
    else:
        g = getattr(obj, "echo_get", None)                 # контуры, холсты, звуки, диапазоны …
        if g is not None:
            return g(name)
        if cls is range:
            if name in ("length", "len", "size"):
                return len(obj)
            m = LIST_METHODS.get(name)
            if m is not None:
                return _Bound(m, list(obj))
    raise EchoError(f"у значения типа «{_type_name(obj)}» нет поля «{name}»", line)


def set_member(obj, name, val, line):
    if obj.__class__ is dict:
        obj[name] = val
        return
    if obj.__class__ is Instance:
        obj.fields[name] = val
        return
    st = getattr(obj, "echo_set", None)
    if st is not None:
        st(name, val)
        return
    raise EchoError(f"нельзя задать поле «{name}» у значения типа «{_type_name(obj)}»", line)


def contains(container, v, line=0):
    if container is None:
        return False
    if container.__class__ is dict or isinstance(container, (list, range, tuple, set)):
        try:
            return v in container
        except TypeError:
            return False
    if container.__class__ is str:
        return _to_str(v) in container
    if container.__class__ is Instance:
        return v in container.fields
    raise EchoError(f"«in» не подходит для значения типа «{_type_name(container)}»", line)


def get_index(obj, idx, line):
    try:
        if obj.__class__ is list or obj.__class__ is str:
            return obj[int(idx)]
        if obj.__class__ is dict:
            return obj.get(idx)
    except IndexError:
        return None
    except (TypeError, ValueError):
        raise EchoError("индекс должен быть числом", line)
    raise EchoError(f"значение типа «{_type_name(obj)}» нельзя индексировать", line)


def set_index(obj, idx, val, line):
    if obj.__class__ is list:
        i = int(idx)
        if -len(obj) <= i < len(obj):
            obj[i] = val
            return
        raise EchoError(f"индекс {i} за границей списка (длина {len(obj)})", line)
    if obj.__class__ is dict:
        obj[idx] = val
        return
    raise EchoError(f"в значение типа «{_type_name(obj)}» нельзя записать по индексу", line)


class Compiler:
    def __init__(self):
        self.widgets: dict[str, WidgetClass] = {}
        self.theme_nodes = []
        self.scene_blocks = []

    # ── выражения ──
    def ex(self, n):
        k = n[0]
        line = n[1]
        if k == "num" or k == "str" or k == "const":
            v = n[2]
            return lambda fr: v
        if k == "name":
            name = n[2]

            def get_name(fr):
                f = fr
                while f is not None:
                    v = f.vars
                    if name in v:
                        return v[name]
                    f = f.parent
                inst = fr.inst
                if inst is not None:
                    fl = inst.fields
                    if name in fl:
                        return fl[name]
                    m = inst.cls.methods.get(name)
                    if m is not None:
                        return BoundMethod(inst, m)
                g = fr.g
                if name in g:
                    return g[name]
                raise EchoError(f"неизвестное имя «{name}»", line)
            return get_name
        if k == "self":
            def get_self(fr):
                if fr.inst is None:
                    raise EchoError("self можно использовать только внутри виджета", line)
                return fr.inst
            return get_self
        if k == "list":
            items = [self.ex(x) for x in n[2]]
            return lambda fr: [f(fr) for f in items]
        if k == "map":
            items = [(key, self.ex(v)) for key, v in n[2]]
            return lambda fr: {key: f(fr) for key, f in items}
        if k == "bin":
            op = BINOPS[n[2]]
            sym = n[2]
            na, nb = n[3], n[4]
            const_kinds = ("num", "const")
            if na[0] in const_kinds and nb[0] in const_kinds:          # 2 * PI_константы — считаем сразу
                try:
                    v = op(na[2], nb[2])
                    return lambda fr: v
                except Exception:                                    # noqa: BLE001
                    pass
            a, b = self.ex(na), self.ex(nb)
            fast = _FAST.get(sym)
            if fast is not None:
                # быстрые пути для чисел: без лишнего вызова op() и общей обработки
                if nb[0] == "num":
                    cb = nb[2]
                    return fast(a, None, cb, line, sym)
                if na[0] == "num":
                    ca = na[2]
                    return fast(None, b, ca, line, sym, left_const=True)
                return fast(a, b, None, line, sym)

            def binop(fr):
                x = a(fr)
                y = b(fr)
                try:
                    return op(x, y)
                except TypeError:
                    raise EchoError(f"операция «{sym}» не подходит для «{_type_name(x)}» и «{_type_name(y)}»", line)
                except OverflowError:
                    raise EchoError("слишком большое число", line)
            return binop
        if k == "neg":
            a = self.ex(n[2])

            def neg(fr):
                v = a(fr)
                try:
                    return -v
                except TypeError:
                    raise EchoError(f"минус не подходит для «{_type_name(v)}»", line)
            return neg
        if k == "not":
            a = self.ex(n[2])
            return lambda fr: not a(fr)
        if k == "and":
            a, b = self.ex(n[2]), self.ex(n[3])
            return lambda fr: a(fr) and b(fr)
        if k == "or":
            a, b = self.ex(n[2]), self.ex(n[3])
            return lambda fr: a(fr) or b(fr)
        if k == "tern":
            c, a, b = self.ex(n[2]), self.ex(n[3]), self.ex(n[4])
            return lambda fr: a(fr) if c(fr) else b(fr)
        if k == "range":
            a, b = self.ex(n[2]), self.ex(n[3])

            def rng(fr):
                lo, hi = a(fr), b(fr)
                try:
                    return range(int(lo), int(hi))
                except (TypeError, ValueError):
                    raise EchoError("границы диапазона a..b должны быть числами", line)
            return rng
        if k == "index":
            o, i = self.ex(n[2]), self.ex(n[3])
            return lambda fr: get_index(o(fr), i(fr), line)
        if k == "member":
            o = self.ex(n[2])
            nm = n[3]
            return lambda fr: get_member(o(fr), nm, line)
        if k == "fnlit":
            params, body = n[2], n[3]
            pnames = [p for p, _ in params]
            pdef = [self.ex(d) if d is not None else None for _, d in params]
            cbody = self.block(body)
            return lambda fr: UserFn("fn", pnames, pdef, cbody, fr, fr.inst)
        if k == "call":
            return self.call(n)
        if k == "in":
            a, b, neg_ = self.ex(n[2]), self.ex(n[3]), n[4]
            if neg_:
                return lambda fr: not contains(b(fr), a(fr), line)
            return lambda fr: contains(b(fr), a(fr), line)
        if k == "tpl":
            parts = [self.ex(x) for x in n[2]]
            ts = _to_str
            return lambda fr: "".join([ts(f(fr)) for f in parts])
        raise EchoError(f"неизвестный узел {k}", line)

    def call(self, n):
        line = n[1]
        callee_node = n[2]
        args = [self.ex(a) for a in n[3]]
        kwargs = [(key, self.ex(v)) for key, v in n[4]]
        if callee_node[0] == "name":
            fname = callee_node[2]
        elif callee_node[0] == "member":
            fname = callee_node[3]
        else:
            fname = "функция"
        callee = self.ex(callee_node)
        by_name = callee_node[0] == "name"

        def do_call(fr):
            f = callee(fr)
            if by_name and not callable(f):
                # поле виджета с тем же именем (clip, visible…) не мешает вызвать встроенную функцию
                gf = fr.g.get(fname)
                if callable(gf):
                    f = gf
            av = [a(fr) for a in args]
            kv = {key: v(fr) for key, v in kwargs} if kwargs else {}
            cls = f.__class__
            if cls is UserFn:
                return call_user(f, av, kv, line)
            if cls is BoundMethod:
                return call_user(f.fn, av, kv, line, f.inst)
            if cls is InstFn:                              # spawn(), emit(), after() … — знают, кто их вызвал
                av.insert(0, fr.inst)
                f = f.fn
            if callable(f) and not isinstance(f, (Instance,)):
                try:
                    return f(*av, **kv)
                except EchoError as e:
                    if not e.line:
                        e.line = line
                    raise
                except TypeError as e:
                    raise EchoError(f"{fname}(): неверные аргументы ({e})", line)
                except (ValueError, ZeroDivisionError, OverflowError) as e:
                    raise EchoError(f"{fname}(): {e}", line)
            raise EchoError(f"«{fname}» — не функция (это {_type_name(f)})", line)
        return do_call

    # ── инструкции ──
    def block(self, stmts):
        compiled = [self.st(s) for s in stmts]
        lines = [s[1] for s in stmts]
        if not compiled:
            return lambda fr: None
        if len(compiled) == 1:
            only = compiled[0]
            ln = lines[0]

            def one(fr):
                try:
                    return only(fr)
                except EchoError:
                    raise
                except RecursionError:
                    raise EchoError("слишком глубокая рекурсия", ln)
                except Exception as e:                       # noqa: BLE001
                    raise EchoError(f"ошибка: {e}", ln)
            return one
        pairs = list(zip(compiled, lines))

        def run(fr):
            for s, ln in pairs:
                try:
                    r = s(fr)
                except EchoError:
                    raise
                except RecursionError:
                    raise EchoError("слишком глубокая рекурсия", ln)
                except Exception as e:                       # noqa: BLE001
                    raise EchoError(f"ошибка: {e}", ln)
                if r is not None:
                    return r
            return None
        return run

    def st(self, n):
        k = n[0]
        line = n[1]
        if k == "expr":
            e = self.ex(n[2])

            def expr_stmt(fr):
                e(fr)
            return expr_stmt
        if k == "let":
            name = n[2]
            e = self.ex(n[3]) if n[3] is not None else (lambda fr: None)

            def let(fr):
                fr.vars[name] = e(fr)
            return let
        if k == "assign":
            return self.assign(n)
        if k == "fndef":
            name, params, body = n[2], n[3], n[4]
            pnames = [p for p, _ in params]
            pdef = [self.ex(d) if d is not None else None for _, d in params]
            cbody = self.block(body)

            def fndef(fr):
                fr.vars[name] = UserFn(name, pnames, pdef, cbody, fr, fr.inst)
            return fndef
        if k == "if":
            arms = [(self.ex(c), self.block(b)) for c, b in n[2]]
            els = self.block(n[3]) if n[3] is not None else None

            def if_(fr):
                for c, b in arms:
                    if c(fr):
                        return b(fr)
                if els is not None:
                    return els(fr)
                return None
            return if_
        if k == "while":
            c = self.ex(n[2])
            b = self.block(n[3])

            def while_(fr):
                while c(fr):
                    tick_budget(line)
                    r = b(fr)
                    if r is not None:
                        if r is _BRK:
                            break
                        if r is _CONT:
                            continue
                        return r
                return None
            return while_
        if k == "for":
            a, bname, it, body = n[2], n[3], self.ex(n[4]), self.block(n[5])

            def for_(fr):
                seq = it(fr)
                v = fr.vars
                if isinstance(seq, dict):
                    items = list(seq.items()) if bname else list(seq.keys())
                elif isinstance(seq, (list, range, str, tuple)):
                    items = list(enumerate(seq)) if bname else seq
                elif isinstance(seq, (int, float)):
                    items = list(enumerate(range(int(seq)))) if bname else range(int(seq))
                else:
                    raise EchoError(f"по значению типа «{_type_name(seq)}» нельзя пройти циклом", line)
                bud = BUDGET
                for item in items:
                    bud[0] -= 1
                    if bud[0] < 0:
                        raise _too_long(line)
                    if bname:
                        v[a], v[bname] = item
                    else:
                        v[a] = item
                    r = body(fr)
                    if r is not None:
                        if r is _BRK:
                            break
                        if r is _CONT:
                            continue
                        return r
                return None
            return for_
        if k == "return":
            e = self.ex(n[2]) if n[2] is not None else (lambda fr: None)
            return lambda fr: _Ret(e(fr))
        if k == "break":
            return lambda fr: _BRK
        if k == "continue":
            return lambda fr: _CONT
        if k == "block":
            return self.block(n[2])
        if k == "try":
            body, name, handler = self.block(n[2]), n[3], self.block(n[4])

            def try_(fr):
                try:
                    return body(fr)
                except EchoError as e:
                    if e.fatal:
                        raise
                    if name:
                        fr.vars[name] = e.msg
                    return handler(fr)
            return try_
        if k == "match":
            subj = self.ex(n[2])
            arms = [([self.ex(p) for p in pats], self.block(body)) for pats, body in n[3]]
            default = self.block(n[4]) if n[4] is not None else None

            def match_(fr):
                v = subj(fr)
                for pats, body in arms:
                    for pf in pats:
                        pv = pf(fr)
                        if pv == v or (pv.__class__ is range and v.__class__ in (int, float) and
                                       pv.start <= v < pv.stop):
                            return body(fr)
                if default is not None:
                    return default(fr)
                return None
            return match_
        if k == "add":
            name, m = n[2], self.ex(n[3])

            def add(fr):
                adder = fr.g.get("__add__")
                if adder is None:
                    raise EchoError("add можно использовать только внутри scene { }", line)
                adder(name, m(fr), line)
            return add
        if k in ("widget", "theme", "scene"):
            raise EchoError(f"«{k}» можно объявлять только на верхнем уровне файла", line)
        raise EchoError(f"неизвестная инструкция {k}", line)

    def assign(self, n):
        line, op, target, val = n[1], n[2], n[3], self.ex(n[4])
        opf = None if op == "=" else BINOPS[op[0]]
        sym = op
        tk = target[0]
        if tk == "name":
            name = target[2]

            def assign_name(fr):
                v = val(fr)
                f = fr
                while f is not None:
                    vs = f.vars
                    if name in vs:
                        if opf is not None:
                            try:
                                v = opf(vs[name], v)
                            except TypeError:
                                raise EchoError(f"«{sym}» не подходит для «{_type_name(vs[name])}»", line)
                        vs[name] = v
                        return
                    f = f.parent
                inst = fr.inst
                if inst is not None and name in inst.fields:
                    if opf is not None:
                        try:
                            v = opf(inst.fields[name], v)
                        except TypeError:
                            raise EchoError(f"«{sym}» не подходит для «{_type_name(inst.fields[name])}»", line)
                    inst.fields[name] = v
                    return
                g = fr.g
                if name in g and name in g.get("__user__", ()):
                    if opf is not None:
                        v = opf(g[name], v)
                    g[name] = v
                    return
                if opf is not None:
                    raise EchoError(f"переменная «{name}» ещё не объявлена", line)
                if name in g and name not in g.get("__user__", ()):
                    raise EchoError(f"«{name}» — встроенное имя, его нельзя перезаписать (используйте let)", line)
                fr.vars[name] = v
            return assign_name
        if tk == "member":
            obj, nm = self.ex(target[2]), target[3]

            def assign_member(fr):
                o = obj(fr)
                v = val(fr)
                if opf is not None:
                    v = opf(get_member(o, nm, line), v)
                set_member(o, nm, v, line)
            return assign_member
        obj, idx = self.ex(target[2]), self.ex(target[3])

        def assign_index(fr):
            o = obj(fr)
            i = idx(fr)
            v = val(fr)
            if opf is not None:
                v = opf(get_index(o, i, line), v)
            set_index(o, i, v, line)
        return assign_index

    # ── программа целиком ──
    def program(self, nodes):
        top = []
        for n in nodes:
            k = n[0]
            if k == "widget":
                self.widget(n)
            elif k == "theme":
                self.theme_nodes.append(self.ex(n[2]))
            elif k == "scene":
                self.scene_blocks.append(self.block(n[2]))
            else:
                top.append(n)
        return self.block(top)

    def widget(self, n):
        _, line, name, props, states, methods, handlers = n
        if name in self.widgets:
            raise EchoError(f"виджет «{name}» объявлен дважды", line)
        cp = [(nm, self.ex(e), ln) for nm, e, ln in props]
        cs = [(nm, self.ex(e), ln) for nm, e, ln in states]
        cm = {}
        for nm, params, body, ln in methods:
            pn = [p for p, _ in params]
            pd = [self.ex(d) if d is not None else None for _, d in params]
            cm[nm] = UserFn(f"{name}.{nm}", pn, pd, self.block(body), None)
        ch = {}
        for ev, params, body, ln in handlers:
            pn = [p for p, _ in params]
            pd = [self.ex(d) if d is not None else None for _, d in params]
            ch[ev] = UserFn(f"{name}.on {ev}", pn, pd, self.block(body), None)
        self.widgets[name] = WidgetClass(name, cp, cs, cm, ch, line)


class Program:
    """Скомпилированный скрипт: верхний уровень, виджеты, тема, сцена."""

    def __init__(self, src: str):
        nodes = parse(src)
        c = Compiler()
        self.top = c.program(nodes)
        self.widgets = c.widgets
        self.theme_nodes = c.theme_nodes
        self.scene_blocks = c.scene_blocks


def compile_program(src: str) -> Program:
    return Program(src)


def bind_globals(widget_fn: UserFn, g: dict):
    """Методы/обработчики виджетов компилируются без кадра — подключаем глобалы при запуске."""
    widget_fn.closure = Frame({}, None, None, g)


def run_handler(fn: UserFn, inst, args, budget=DEFAULT_BUDGET):
    """Вызвать обработчик виджета с бюджетом шагов."""
    BUDGET[0] = budget
    return call_user(fn, list(args), {}, fn and 0, inst)


# небольшая проверка «на глаз»: python dsl_lang.py
if __name__ == "__main__":
    src = """
    let total = 0
    fn sq(x) { return x * x }
    for i in 0..10 { total += sq(i) }
    let s = "итог: " + total
    """
    p = compile_program(src)
    g = {"__user__": set()}
    BUDGET[0] = DEFAULT_BUDGET
    fr = Frame({}, None, None, g)
    p.top(fr)
    print(fr.vars["s"], math.pi)
