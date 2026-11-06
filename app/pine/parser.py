"""Recursive-descent parser: tokens -> AST (plain tuples/dataclasses)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .lexer import PineSyntaxError, Tok, tokenize

TYPE_WORDS = {"int", "float", "bool", "string", "color", "series", "simple", "const", "line", "label", "box", "table"}


# ---- AST nodes ---------------------------------------------------------------
@dataclass
class Num: value: float
@dataclass
class Str: value: str
@dataclass
class Name: name: str            # may be dotted, e.g. "ta.sma" or "strategy.long"
@dataclass
class Index: target: object; offset: object
@dataclass
class Call: func: str; args: list; kwargs: dict; line: int = 0
@dataclass
class Unary: op: str; operand: object
@dataclass
class Binary: op: str; left: object; right: object
@dataclass
class Ternary: cond: object; a: object; b: object
@dataclass
class TupleLit: items: list
@dataclass
class Assign: targets: list; value: object; reassign: bool; line: int = 0; op: str = "="
@dataclass
class If: cond: object; body: list; orelse: list; line: int = 0
@dataclass
class ExprStmt: expr: object; line: int = 0
@dataclass
class FuncDef: name: str; params: list; body: list
@dataclass
class For: var: str; start: object; end: object; body: list; line: int = 0


class Parser:
    def __init__(self, src: str):
        self.toks: list[Tok] = tokenize(src)
        self.i = 0

    # helpers
    @property
    def t(self) -> Tok:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def is_op(self, *ops) -> bool:
        return self.t.kind == "OP" and self.t.value in ops

    def is_kw(self, *words) -> bool:
        return self.t.kind == "ID" and self.t.value in words

    def eat(self, kind: str, value=None) -> Tok:
        tok = self.t
        if tok.kind != kind or (value is not None and tok.value != value):
            want = value if value is not None else kind
            got = tok.value if tok.value is not None else tok.kind
            raise PineSyntaxError(f"Expected {want!r} but found {got!r}", tok.line)
        self.i += 1
        return tok

    def skip_newlines(self):
        while self.t.kind == "NEWLINE":
            self.i += 1

    # program / statements
    def parse(self) -> list:
        stmts = []
        self.skip_newlines()
        while self.t.kind != "EOF":
            stmts.extend(self.statement())
            self.skip_newlines()
        return stmts

    def block(self) -> list:
        self.eat("NEWLINE")
        self.eat("INDENT")
        stmts = []
        self.skip_newlines()
        while self.t.kind not in ("DEDENT", "EOF"):
            stmts.extend(self.statement())
            self.skip_newlines()
        if self.t.kind == "DEDENT":
            self.i += 1
        return stmts

    def end_stmt(self):
        if self.t.kind == "NEWLINE":
            self.i += 1
        elif self.t.kind not in ("EOF", "DEDENT"):
            raise PineSyntaxError(f"Unexpected {self.t.value!r}", self.t.line)

    def statement(self) -> list:
        line = self.t.line
        if self.is_kw("if"):
            return [self.if_stmt()]
        if self.is_kw("for"):
            return [self.for_stmt()]
        if self.is_kw("import", "export", "type", "method", "switch", "while"):
            raise PineSyntaxError(f"'{self.t.value}' is not supported by this Pine engine", line)

        # function definition: name(params) =>
        if self.t.kind == "ID" and self.peek().kind == "OP" and self.peek().value == "(":
            j, depth = self.i + 1, 0
            while j < len(self.toks):
                tk = self.toks[j]
                if tk.kind == "OP" and tk.value == "(":
                    depth += 1
                elif tk.kind == "OP" and tk.value == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif tk.kind in ("NEWLINE", "EOF"):
                    break
                j += 1
            nxt = self.toks[j + 1] if j + 1 < len(self.toks) else None
            if nxt and nxt.kind == "OP" and nxt.value == "=>":
                return [self.func_def()]

        # tuple destructuring: [a, b, c] = expr
        if self.is_op("["):
            save = self.i
            try:
                self.i += 1
                names = [self.eat("ID").value]
                while self.is_op(","):
                    self.i += 1
                    names.append(self.eat("ID").value)
                self.eat("OP", "]")
                if self.is_op("=", ":="):
                    re = self.t.value == ":="
                    self.i += 1
                    val = self.expr()
                    self.end_stmt()
                    return [Assign(names, val, re, line)]
            except PineSyntaxError:
                pass
            self.i = save

        # declarations: [var|varip] [type] name = expr
        save = self.i
        while self.is_kw("var", "varip") or (self.is_kw(*TYPE_WORDS) and self.peek().kind == "ID"):
            self.i += 1
        if self.t.kind == "ID" and self.peek().kind == "OP" and self.peek().value in ("=", ":=", "+=", "-=", "*=", "/="):
            name = self.eat("ID").value
            op = self.eat("OP").value
            val = self.expr()
            self.end_stmt()
            return [Assign([name], val, op != "=", line, op)]
        self.i = save

        e = self.expr()
        self.end_stmt()
        return [ExprStmt(e, line)]

    def func_def(self) -> FuncDef:
        name = self.eat("ID").value
        self.eat("OP", "(")
        params = []
        while not self.is_op(")"):
            while self.is_kw(*TYPE_WORDS) and self.peek().kind == "ID":
                self.i += 1
            params.append(self.eat("ID").value)
            if self.is_op("="):  # default values -> ignored
                self.i += 1
                self.expr()
            if self.is_op(","):
                self.i += 1
        self.eat("OP", ")")
        self.eat("OP", "=>")
        if self.t.kind == "NEWLINE":
            body = self.block()
        else:
            body = [ExprStmt(self.expr(), self.t.line)]
            self.end_stmt()
        return FuncDef(name, params, body)

    def if_stmt(self) -> If:
        line = self.eat("ID", "if").line
        cond = self.expr()
        body = self.block()
        orelse = []
        save = self.i
        self.skip_newlines()
        if self.is_kw("else"):
            self.i += 1
            orelse = [self.if_stmt()] if self.is_kw("if") else self.block()
        else:
            self.i = save
        return If(cond, body, orelse, line)

    def for_stmt(self) -> For:
        line = self.eat("ID", "for").line
        var = self.eat("ID").value
        self.eat("OP", "=")
        start = self.expr()
        self.eat("ID", "to")
        end = self.expr()
        body = self.block()
        return For(var, start, end, body, line)

    # expressions (lowest -> highest precedence)
    def expr(self):
        return self.ternary()

    def ternary(self):
        cond = self.or_()
        if self.is_op("?"):
            self.i += 1
            a = self.ternary()
            self.eat("OP", ":")
            b = self.ternary()
            return Ternary(cond, a, b)
        return cond

    def or_(self):
        left = self.and_()
        while self.is_kw("or"):
            self.i += 1
            left = Binary("or", left, self.and_())
        return left

    def and_(self):
        left = self.not_()
        while self.is_kw("and"):
            self.i += 1
            left = Binary("and", left, self.not_())
        return left

    def not_(self):
        if self.is_kw("not"):
            self.i += 1
            return Unary("not", self.not_())
        return self.equality()

    def equality(self):
        left = self.comparison()
        while self.is_op("==", "!="):
            op = self.eat("OP").value
            left = Binary(op, left, self.comparison())
        return left

    def comparison(self):
        left = self.additive()
        while self.is_op("<", ">", "<=", ">="):
            op = self.eat("OP").value
            left = Binary(op, left, self.additive())
        return left

    def additive(self):
        left = self.term()
        while self.is_op("+", "-"):
            op = self.eat("OP").value
            left = Binary(op, left, self.term())
        return left

    def term(self):
        left = self.unary()
        while self.is_op("*", "/", "%"):
            op = self.eat("OP").value
            left = Binary(op, left, self.unary())
        return left

    def unary(self):
        if self.is_op("-", "+"):
            op = self.eat("OP").value
            return Unary(op, self.unary())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while True:
            if self.is_op("("):
                if not isinstance(node, Name):
                    raise PineSyntaxError("Only named functions can be called", self.t.line)
                node = self.call(node.name)
            elif self.is_op("["):
                self.i += 1
                off = self.expr()
                self.eat("OP", "]")
                node = Index(node, off)
            elif self.is_op(".") and isinstance(node, Name):
                self.i += 1
                node = Name(node.name + "." + self.eat("ID").value)
            else:
                return node

    def call(self, fname: str) -> Call:
        line = self.eat("OP", "(").line
        args, kwargs = [], {}
        while not self.is_op(")"):
            if self.t.kind == "ID" and self.peek().kind == "OP" and self.peek().value == "=":
                k = self.eat("ID").value
                self.i += 1
                kwargs[k] = self.expr()
            else:
                args.append(self.expr())
            if self.is_op(","):
                self.i += 1
            elif not self.is_op(")"):
                raise PineSyntaxError(f"Expected ',' or ')' in call to {fname}", self.t.line)
        self.eat("OP", ")")
        return Call(fname, args, kwargs, line)

    def primary(self):
        tok = self.t
        if tok.kind == "NUM":
            self.i += 1
            return Num(tok.value)
        if tok.kind == "STR":
            self.i += 1
            return Str(tok.value)
        if tok.kind == "COLOR":
            self.i += 1
            return Str(tok.value)
        if tok.kind == "ID":
            self.i += 1
            return Name(tok.value)
        if self.is_op("("):
            self.i += 1
            e = self.expr()
            self.eat("OP", ")")
            return e
        if self.is_op("["):
            self.i += 1
            items = []
            while not self.is_op("]"):
                items.append(self.expr())
                if self.is_op(","):
                    self.i += 1
            self.eat("OP", "]")
            return TupleLit(items)
        raise PineSyntaxError(f"Unexpected {tok.value if tok.value is not None else tok.kind!r}", tok.line)


def parse(src: str) -> list:
    return Parser(src).parse()
