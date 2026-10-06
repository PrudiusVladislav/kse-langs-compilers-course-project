START, IDENT, NUMBER, PAIR, GLYPH = "START", "IDENT", "NUMBER", "PAIR", "GLYPH"

GLYPHS = {
    "3⃣": "kw_i32",
    "6⃣": "kw_i64",
    "❓": "kw_bool",
    "\U0001f513": "kw_mut",
    "\U0001f914": "kw_if",
    "\U0001f643": "kw_else",
    "\U0001f501": "kw_while",
    "\U0001f6aa": "kw_exit",
    "✅": "kw_true",
    "❌": "kw_false",
    "⟨": "langle",
    "⟩": "rangle",
    "≠": "ne",
}
CATEGORIES = {
    "kw_i32": ("keyword", "typename"),
    "kw_i64": ("keyword", "typename"),
    "kw_bool": ("keyword", "typename"),
    "kw_mut": ("keyword", "specifier"),
    "kw_exit": ("keyword", "statement"),
    "kw_if": ("keyword", "statement"),
    "kw_else": ("keyword", "statement"),
    "kw_while": ("keyword", "statement"),
    "kw_true": ("constant", "boolean"),
    "kw_false": ("constant", "boolean"),
    "ident": ("identifier", None),
    "number": ("constant", "numeric"),
    "lbrace": ("block", "start"),
    "rbrace": ("block", "end"),
    "langle": ("initialiser", "start"),
    "rangle": ("initialiser", "end"),
    "lparen": ("group", "start"),
    "rparen": ("group", "end"),
    "assign": ("operator", "assignment"),
    "plus": ("operator", "arithmetic"),
    "minus": ("operator", "arithmetic"),
    "star": ("operator", "arithmetic"),
    "eq": ("operator", "comparison"),
    "ne": ("operator", "comparison"),
    "not": ("operator", "logical"),
    "endline": ("endline", None),
}
SINGLES = {
    ord("{"): "lbrace",
    ord("}"): "rbrace",
    ord("("): "lparen",
    ord(")"): "rparen",
    ord("+"): "plus",
    ord("-"): "minus",
    ord("*"): "star",
    ord("!"): "not",
}
PAIRS = {
    ord(":"): ("assign", ":=", "':' is not followed by '='"),
    ord("="): ("eq", "==", "expected '==' (a single '=' is not an operator)"),
}
KEYCAP = b"\x83\xa3"
TYPE_DIGITS = {b"3": "kw_i32", b"6": "kw_i64"}


class CompileError(Exception):
    def __init__(self, line, col, message):
        super().__init__(message)
        self.line = line
        self.col = col
        self.message = message


class Token:
    def __init__(self, kind, text, line, col):
        self.kind = kind
        self.text = text
        self.line = line
        self.col = col

    def __repr__(self):
        category, sub = CATEGORIES[self.kind]
        text = "\\n" if self.kind == "endline" else self.text
        parts = [text, category] + ([sub] if sub else [])
        return f"({', '.join(parts)} @{self.line}:{self.col})"


def is_alpha(b):
    return b is not None and (65 <= b <= 90 or 97 <= b <= 122 or b == 95)


def is_digit(b):
    return b is not None and 48 <= b <= 57


def byte_text(b):
    return chr(b) if 32 <= b <= 126 else f"\\x{b:02x}"


def lex(data):
    lines, tokens = [], []
    state, start, start_col = START, 0, 1
    line, col = 1, 1
    i = 0

    while i <= len(data):
        b = data[i] if i < len(data) else None

        if state == START:
            if b is None:
                break
            elif b in (32, 9):
                pass
            elif b == 10:
                tokens.append(Token("endline", "\n", line, col))
                lines.append(tokens)
                tokens = []
                line, col = line + 1, 0
            elif is_alpha(b):
                state, start, start_col = IDENT, i, col
            elif is_digit(b):
                state, start, start_col = NUMBER, i, col
            elif b in PAIRS:
                state, pair, start_col = PAIR, PAIRS[b], col
            elif b in SINGLES:
                tokens.append(Token(SINGLES[b], chr(b), line, col))
            elif b >= 0xC0:
                state, start, start_col = GLYPH, i, col
            else:
                raise CompileError(line, col, f"unexpected byte '{byte_text(b)}'")

        elif state == IDENT:
            if is_alpha(b) or is_digit(b):
                pass
            else:
                tokens.append(Token("ident", data[start:i].decode(), line, start_col))
                state = START
                continue

        elif state == NUMBER:
            if is_digit(b):
                pass
            elif is_alpha(b):
                raise CompileError(line, col, f"unexpected byte '{byte_text(b)}' in a number")
            elif b == 0xE2 and data[i + 1 : i + 3] == KEYCAP:
                run = data[start:i]
                if run not in TYPE_DIGITS:
                    raise CompileError(
                        line, start_col, f"a keycap may not follow the number {run.decode()}"
                    )
                after = data[i + 3] if i + 3 < len(data) else None
                if is_digit(after):
                    raise CompileError(line, col + 1, "a number may not follow a type")
                tokens.append(Token(TYPE_DIGITS[run], run.decode() + "⃣", line, start_col))
                state = START
                i, col = i + 3, col + 1
                continue
            else:
                tokens.append(Token("number", data[start:i].decode(), line, start_col))
                state = START
                continue

        elif state == GLYPH:
            if b is not None and 0x80 <= b <= 0xBF:
                pass
            else:
                glyph = data[start:i].decode(errors="replace")
                if glyph not in GLYPHS:
                    raise CompileError(line, start_col, f"unknown glyph '{glyph}'")
                tokens.append(Token(GLYPHS[glyph], glyph, line, start_col))
                state = START
                continue

        elif state == PAIR:
            kind, text, message = pair
            if b == ord("="):
                tokens.append(Token(kind, text, line, start_col))
                state = START
            else:
                raise CompileError(line, start_col, message)

        if b is not None and (b < 0x80 or b >= 0xC0):
            col += 1
        i += 1

    if tokens:
        lines.append(tokens)
    return lines


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <input>", file=sys.stderr)
        sys.exit(2)

    try:
        with open(sys.argv[1], "rb") as f:
            data = f.read()
    except OSError as exc:
        print(f"cannot read {sys.argv[1]}: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        lines = lex(data)
    except CompileError as exc:
        print(f"compilation error: line {exc.line}:{exc.col}: {exc.message}", file=sys.stderr)
        sys.exit(1)

    for tokens in lines:
        print(" ".join(repr(t) for t in tokens))
