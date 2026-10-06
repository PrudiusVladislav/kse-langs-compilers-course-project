"""Port the practice-5 test corpus to the stage-1 syntax.

Run once, from the repo root. Rewrites tests/**/*.txt, input.txt and
lexer_demo.txt in place. The .expected and .ast files are regenerated from the
compiler, not ported, so this script leaves them alone.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent

I32, I64 = "3⃣", "6⃣"
KEYWORDS = {
    "i32": I32,
    "i64": I64,
    "bool": "❓",
    "mut": "\U0001f513",
    "if": "\U0001f914",
    "else": "\U0001f643",
    "while": "\U0001f501",
    "exit": "\U0001f6aa",
    "true": "✅",
    "false": "❌",
}
SOURCES = ["input.txt", "lexer_demo.txt", "tests/ok/*.txt", "tests/err/*.txt"]

VARIATION_SELECTOR = "️"


def is_word(b):
    return b.isalnum() or b == "_"


def words(line):
    """Split into alternating word / non-word runs, longest run at a time."""
    runs, start = [], 0
    for i in range(1, len(line) + 1):
        if i == len(line) or is_word(line[i]) != is_word(line[start]):
            runs.append(line[start:i])
            start = i
    return runs


def port_line(line):
    out = []
    for run in words(line):
        out.append(KEYWORDS.get(run, run) if is_word(run[:1]) else run)
    line = "".join(out)

    line = line.replace("!=", "≠")
    return brace_to_angle(line)


def brace_to_angle(line):
    """`x{expr}` is an initialiser; a brace alone on its line is a block."""
    if line.strip() in ("{", "}"):
        return line
    return line.replace("{", " ⟨ ").replace("}", " ⟩ ")


def tidy(line):
    """Collapse the spaces brace_to_angle introduced, keeping indentation."""
    indent = line[: len(line) - len(line.lstrip())]
    return indent + " ".join(line.split())


def port(path):
    lines = path.read_text().splitlines()
    ported = [tidy(port_line(l)) if l.strip() else l for l in lines]
    text = "\n".join(ported) + "\n"

    if VARIATION_SELECTOR in text:
        raise SystemExit(
            f"{path}: U+FE0F in the output. An editor inserted a variation "
            f"selector, which makes 3⃣ seven bytes instead of four and will "
            f"not lex."
        )

    path.write_text(text)
    return sum(a != b for a, b in zip(lines, ported))


def main():
    paths = []
    for pattern in SOURCES:
        paths.extend(sorted(ROOT.glob(pattern)) if "*" in pattern else [ROOT / pattern])

    if missing := [p for p in paths if not p.exists()]:
        print(f"missing: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 2

    total = 0
    for path in paths:
        changed = port(path)
        total += changed
        print(f"{path.relative_to(ROOT)}: {changed} lines")
    print(f"\n{len(paths)} files, {total} lines changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
