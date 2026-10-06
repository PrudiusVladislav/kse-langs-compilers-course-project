# Kaguya — language design, stage 1

A small 32-bit integer language with emoji keywords. It keeps the shape of the
practice-5 language — one statement per line, mandatory trailing exit, three
types — and changes the surface: every keyword is a multibyte glyph, an
initialiser is delimited by `⟨ ⟩`, and `{ }` means a block and nothing else.

## Glyphs

| Glyph | Token kind | Bytes (hex) | Codepoints |
|-------|-----------|-------------|------------|
| `3⃣` | `kw_i32` | `33 E2 83 A3` | U+0033 U+20E3 |
| `6⃣` | `kw_i64` | `36 E2 83 A3` | U+0036 U+20E3 |
| `❓` | `kw_bool` | `E2 9D 93` | U+2753 |
| `🔓` | `kw_mut` | `F0 9F 94 93` | U+1F513 |
| `🤔` | `kw_if` | `F0 9F A4 94` | U+1F914 |
| `🙃` | `kw_else` | `F0 9F 99 83` | U+1F643 |
| `🔁` | `kw_while` | `F0 9F 94 81` | U+1F501 |
| `🚪` | `kw_exit` | `F0 9F 9A AA` | U+1F6AA |
| `✅` | `kw_true` | `E2 9C 85` | U+2705 |
| `❌` | `kw_false` | `E2 9D 8C` | U+274C |
| `⟨` | `langle` | `E2 9F A8` | U+27E8 |
| `⟩` | `rangle` | `E2 9F A9` | U+27E9 |
| `≠` | `ne` | `E2 89 A0` | U+2260 |

ASCII tokens: `:=` assign, `==` eq, `!` not, `+` `-` `*`, `(` `)`, `{` `}`.

No ASCII keyword survives. The `IDENT` state still runs, but its output is
always `ident` — there is no keyword table behind it any more.

## The language

```
3⃣ x ⟨ 2 + 3 * 4 ⟩        const i32, initialiser mandatory
3⃣ 🔓 total ⟨ 0 ⟩          mutable i32
6⃣ 🔓 big ⟨ 100 ⟩          mutable i64
❓ ok ⟨ x ≠ total ⟩        bool
total := (x + 1) * 2       assignment; only a 🔓 variable may be assigned
3⃣ neg ⟨ -x ⟩              unary minus
❓ b ⟨ !(x == total) ⟩     logical not
🚪 total                   exit, the last statement of the program
```

Mutability is const by default; `🔓` after the type makes a variable mutable.
That keeps the decl line starting with its type token, so `parse_statement`
dispatches on one token exactly as before.

Control flow keeps the practice-5 line discipline: the `🤔` and its condition
share a line, and `{`, `}` and `🙃` each stand alone on theirs.

```
🔁 total ≠ 0
{
    total := total - 1
}
🤔 total == 0
{
    🚪 ✅
}
🙃
{
    🚪 ❌
}
```

## Decisions and what they cost

**`⟨ ⟩` for initialisers, `{ }` for blocks.** In practice 5 `{` meant both, which
is why a brace on the `if` line had to be a special-cased error. One symbol, one
job: the parser never needs lookahead to tell an initialiser from a block.

**Parentheses and unary minus.** `factor` gains `"(" expr ")"` and a `-` prefix.
Both recurse on `factor`, so unary binds tighter than `*` with no extra rule.
`(2 + 3) * 4` is 20. The practice-5 error "negative numbers are not supported"
is gone — `-x` is now a tree, `Neg`, not a rejection.

Unary minus meets the width boundaries head on. `-2147483648` parses as `Neg`
over `Const 2147483648`, and 2147483648 on its own is already too wide for i32 —
so a literal-minded checker rejects the exact minimum of the type, a value that
plainly fits. The same happens at `-9223372036854775808` for i64.

**The checker folds instead.** `visit_neg` collapses `Neg` over a `ConstNode`
into a negative `ConstNode` *before* the range check, and `visit_const` ranges on
signed bounds: −2³¹ … 2³¹−1 for i32, −2⁶³ … 2⁶³−1 for i64. Both minimums are
therefore legal and `-2147483649` is not.

The fold lives in the semantic pass, not the parser. `--ast` still prints `Neg`
over `Const 2147483648`, so the tree mirrors the source and the handout's
"grammar first, nodes second, parser third" order is respected — the parser
stays ignorant of what a number means.

**`==` and `≠`.** Asymmetric on purpose: `==` is the operator every reader knows,
`≠` is the one that reads better than `!=` beside emoji keywords. The cost is two
mechanisms in the lexer where one would do — `PAIRS` still handles `=`, and `≠`
sits in the multibyte table. The error for a lone `=` therefore survives.

`!` is no longer one of them. In practice 5 it needed a state of its own to see
whether an `=` followed; with `≠` spelled as its own glyph there is nothing left
to decide, so `!` collapses to a plain one-byte entry and the `BANG` state is
deleted. A source `!=` is now `!` followed by a lone `=`, which the `=` error
catches.

Internally the operator is stored as the ASCII string `"!="`, not `"≠"`:
`llvmlite`'s `icmp_signed` takes an LLVM predicate and rejects `"≠"` outright.
`--ast` therefore prints `BinOp !=`, which is the one place the tree does not
echo the source.

**No functions.** The handout requires none, and every construct in the grammar
is a stage-2 codegen bill. The program stays a flat statement list.

**Three types.** Exactly the required set. Adding a width would only enlarge the
stage-2 widening lattice without touching anything stage 1 is graded on.

**Columns count characters, not bytes.** `col` advances once per decoded
character: the main loop increments it only on a UTF-8 lead byte (`b < 0x80` or
`b >= 0xC0`). With a 4-byte glyph on every line, byte columns would put every
error message several positions past where an editor's cursor sits, and the
`.expected` files could no longer be checked by hand.

## The keycap collision

`3⃣` is `U+0033 U+20E3` — the ASCII byte `33` followed by a combining enclosing
keycap. In `START` the byte `3` is therefore ambiguous between a number literal
and the `i32` type keyword, and the state machine cannot decide until it has
seen three more bytes.

`NUMBER` resolves it. On `0xE2` after a digit run it peeks the next two bytes:

- `E2 83 A3` and the run is exactly `3` → `kw_i32`
- `E2 83 A3` and the run is exactly `6` → `kw_i64`
- `E2 83 A3` and the run is anything else → error, a keycap on a multi-digit
  number
- any other continuation → the run is a `number` and the `0xE2` is reported
  where it stands

A digit immediately after a type glyph (`3⃣5`) is an error too: the keycap ends
the token, and a number may not follow a type without a name between them.

This is lookahead inside a hand-written state machine — no regex, no `split()`,
no generator. It buys two lexer error tests that nothing else in the language
would exercise.

## Errors

One line to stderr, exit non-zero, no output file:

```
compilation error: line 1:9: unexpected byte '$'
```

Both numbers 1-based; the column is the offending token's own first character.
For a statement after `🚪`, the position is the statement, not the `🚪`.
