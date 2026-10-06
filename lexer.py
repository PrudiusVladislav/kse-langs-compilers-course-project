START, IDENT, NUMBER, PAIR, BANG = "START", "IDENT", "NUMBER", "PAIR", "BANG"

KEYWORDS = {
    "i32": "kw_i32",
    "i64": "kw_i64",
    "bool": "kw_bool",
    "mut": "kw_mut",
    "exit": "kw_exit",
    "if": "kw_if",
    "else": "kw_else",
    "while": "kw_while",
    "true": "kw_true",
    "false": "kw_false",
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
    ord("+"): "plus",
    ord("-"): "minus",
    ord("*"): "star",
}
PAIRS = {
    ord(":"): ("assign", ":=", "':' is not followed by '='"),
    ord("="): ("eq", "==", "expected '==' (a single '=' is not an operator)"),
}


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
            elif b == ord("!"):
                state, start_col = BANG, col
            elif b in SINGLES:
                tokens.append(Token(SINGLES[b], chr(b), line, col))
            else:
                raise CompileError(line, col, f"unexpected byte '{byte_text(b)}'")

        elif state == IDENT:
            if is_alpha(b) or is_digit(b):
                pass
            else:
                word = data[start:i].decode()
                tokens.append(Token(KEYWORDS.get(word, "ident"), word, line, start_col))
                state = START
                continue

        elif state == NUMBER:
            if is_digit(b):
                pass
            elif is_alpha(b):
                raise CompileError(line, col, f"unexpected byte '{byte_text(b)}' in a number")
            else:
                tokens.append(Token("number", data[start:i].decode(), line, start_col))
                state = START
                continue

        elif state == BANG:
            if b == ord("="):
                tokens.append(Token("ne", "!=", line, start_col))
                state = START
            else:
                tokens.append(Token("not", "!", line, start_col))
                state = START
                continue

        elif state == PAIR:
            kind, text, message = pair
            if b == ord("="):
                tokens.append(Token(kind, text, line, start_col))
                state = START
            else:
                raise CompileError(line, start_col, message)

        i += 1
        col += 1

    if tokens:
        lines.append(tokens)
    return lines


def byte_text(b):
    return chr(b) if 32 <= b <= 126 else f"\\x{b:02x}"


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
