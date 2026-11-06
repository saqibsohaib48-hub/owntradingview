"""Pine Script tokenizer with indentation-based blocks (INDENT / DEDENT tokens)."""
from __future__ import annotations

import re
from dataclasses import dataclass


class PineSyntaxError(Exception):
    def __init__(self, msg: str, line: int | None = None):
        super().__init__(f"Line {line}: {msg}" if line else msg)
        self.line = line


@dataclass
class Tok:
    kind: str   # NUM STR ID OP NEWLINE INDENT DEDENT EOF COLOR
    value: object
    line: int


_TOKEN_RE = re.compile(r"""
    (?P<ws>[ \t]+)
  | (?P<comment>//[^\n]*)
  | (?P<color>\#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b)
  | (?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
  | (?P<id>[A-Za-z_][A-Za-z_0-9]*)
  | (?P<op>:=|==|!=|<=|>=|=>|\+=|-=|\*=|/=|[-+*/%<>=?:,()\[\].])
  | (?P<nl>\n)
""", re.VERBOSE)

# A line ending with one of these continues on the next line.
_CONTINUATION_OPS = {"+", "-", "*", "/", "%", "?", ":", ",", "==", "!=", "<", ">", "<=", ">=",
                     "and", "or", "not", "=", ":="}


def tokenize(src: str) -> list[Tok]:
    src = src.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    if not src.endswith("\n"):
        src += "\n"
    toks: list[Tok] = []
    indents = [0]
    depth = 0          # bracket nesting: newlines inside brackets are ignored
    line = 1
    pos = 0
    at_line_start = True

    while pos < len(src):
        if at_line_start and depth == 0:
            # Measure indentation of this line; skip blank/comment-only lines.
            m = re.match(r"[ ]*", src[pos:])
            width = len(m.group(0))
            rest = src[pos + width:]
            if rest.startswith("\n") or rest.startswith("//"):
                end = src.find("\n", pos)
                pos = end + 1
                line += 1
                continue
            prev = toks[-1] if toks else None
            continuing = prev is not None and prev.kind == "NEWLINE" and getattr(prev, "_cont", False)
            if continuing:
                toks.pop()  # join with previous line
            elif width > indents[-1]:
                indents.append(width)
                toks.append(Tok("INDENT", width, line))
            else:
                while width < indents[-1]:
                    indents.pop()
                    toks.append(Tok("DEDENT", width, line))
                if width != indents[-1]:
                    raise PineSyntaxError("Inconsistent indentation", line)
            pos += width
            at_line_start = False
            continue

        m = _TOKEN_RE.match(src, pos)
        if not m:
            raise PineSyntaxError(f"Unexpected character {src[pos]!r}", line)
        kind = m.lastgroup
        text = m.group(kind)
        pos = m.end()
        if kind in ("ws", "comment"):
            continue
        if kind == "nl":
            line += 1
            if depth == 0:
                t = Tok("NEWLINE", None, line - 1)
                last = toks[-1] if toks else None
                t._cont = bool(last and last.kind in ("OP", "ID") and last.value in _CONTINUATION_OPS)  # type: ignore[attr-defined]
                if last and last.kind not in ("NEWLINE", "INDENT", "DEDENT"):
                    toks.append(t)
                at_line_start = True
            continue
        if kind == "num":
            v = float(text)
            toks.append(Tok("NUM", int(v) if re.fullmatch(r"\d+", text) else v, line))
        elif kind == "str":
            toks.append(Tok("STR", bytes(text[1:-1], "utf-8").decode("unicode_escape"), line))
        elif kind == "color":
            toks.append(Tok("COLOR", text, line))
        elif kind == "id":
            toks.append(Tok("ID", text, line))
        else:
            if text in "([":
                depth += 1
            elif text in ")]":
                depth = max(0, depth - 1)
            toks.append(Tok("OP", text, line))

    if toks and toks[-1].kind != "NEWLINE":
        toks.append(Tok("NEWLINE", None, line))
    while len(indents) > 1:
        indents.pop()
        toks.append(Tok("DEDENT", 0, line))
    toks.append(Tok("EOF", None, line))
    return toks
