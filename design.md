# Kaguya — language design

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

**No functions in stage 1.** The handout required none, and every construct in
the grammar is a stage-2 codegen bill. Stage 2 adds them below.

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

# Stage 2 — functions, structures, read and print

Every stage-1 program stays valid and means the same thing. The new constructs
only add statement starts (`🧩`, `🧱`, `📢`, an ident followed by an ident, `(`
or `.`) and one factor (`📥`, a call, a chain).

## New tokens

| Glyph | Token kind | Bytes (hex) | Codepoints |
|-------|-----------|-------------|------------|
| `🧩` | `kw_fn` | `F0 9F A7 A9` | U+1F9E9 |
| `🧱` | `kw_struct` | `F0 9F A7 B1` | U+1F9F1 |
| `📥` | `kw_read` | `F0 9F 93 A5` | U+1F4E5 |
| `📢` | `kw_print` | `F0 9F 93 A2` | U+1F4E2 |
| `→` | `arrow` | `E2 86 92` | U+2192 |

ASCII singles `,` (`comma`) and `.` (`dot`). A string literal is `"` … `"` on one
line: no escapes, any bytes but `"` and a newline, and an unclosed literal is an
error at its opening quote. No glyph has a variation selector, so each one is a
single codepoint.

## Functions

**Rule.** A function is declared only at top level, never in a block. It has a
name, zero or more parameters (each a type and a name, passed by value,
constant in the body) and exactly one result type: `i32`, `i64` or `bool`.
Objects are never parameters or results. A function may call itself and any
function declared above or below it. A function name is declared once.

```
🧩 add(3⃣ a, 3⃣ b) → 3⃣
{
    3⃣ s ⟨a + b⟩
    🚪 s
}
```

**Rule.** `🚪` leaves the body it is in. In a function it returns its value, and
that value's type is the result type, with an `i32` accepted for an `i64`.
At top level it prints and ends the program, as in stage 1. A function body
ends with `🚪`, like the program, and a `🚪` inside a nested block also leaves.
`parse_body` is reused unchanged.

**Rule.** A call is a factor. It takes as many arguments as there are parameters,
and each argument's type is the parameter's type, with `i32` → `i64` widening
only. Arguments are full expressions and may be calls. A call may also stand
alone on a line as a statement, and its value is then discarded.

```
3⃣ r ⟨add(1, add(2, 3))⟩
log(r)
```

A line starting with an ident is an object decl if the next token is an ident
or `🔓`. Otherwise the parser reads a chain: `:=` after it makes an assignment,
and a chain ending in a call makes a call statement. Anything else is an error.

## Structures

**Rule.** A structure is declared only at top level, with a unique name and at
least one field. A field is a type and a name, unique within the structure. The
type is `i32`, `i64`, `bool`, or a structure declared above, so a structure can
never contain itself. Fields come one per line, before the member functions.
The field order is the gep index order and the print order.

**Rule.** A field is constant by default, and `🔓` after the type makes it
mutable, exactly as for variables.

```
🧱 Point
{
    3⃣ 🔓 x
    3⃣ y
    🧩 d2() → 3⃣
    {
        🚪 x * x + y * y
    }
}
🧱 Circle
{
    Point 🔓 center
    3⃣ r
}
```

**Rule.** An object is a variable whose type is a structure declared above. It is
created with one value per field, in field order: no more, no fewer. Each value
is checked like an initialiser, and a structure-typed field takes a nested
`⟨ ⟩`. It is constant by default, `🔓` makes it mutable, and it is never copied:
an object is not a value of `:=`, an argument, a result, or the value of `🚪`.

```
Point 🔓 p ⟨📥, 4⟩
Circle 🔓 c ⟨⟨0, 0⟩, 5⟩
```

**Rule.** `.` selects a field by name, left to right, and the field must exist in
the structure of the expression on its left. A chain may end in a member call.
A field is assigned only through a chain that is mutable at every step: the
root variable is `🔓` and so is every field on the path, the last included. The
error is reported at the first constant step.

```
c.center.x := 3      ok
c.r := 6             error at r: not 🔓
🚪 c.center.d2()
```

## Member functions

**Rule.** A member function is declared inside `🧱` after the fields, with the
same `🧩` header, and is called as `obj.f(args)` with the call rules above. Its
body sees the fields and every member of its structure by bare name, in any
order.

**Rule.** A member marked `🔓` is mutating. Only a mutating member may assign a
field (`x := …`, with the field itself `🔓`) or call another mutating member by
bare name. An unmarked member that does either is an error. A mutating member is
called only through a chain that is mutable at every step, as for a field
assignment: calling one on a constant object is an error.

```
🧩 🔓 move(3⃣ d) → 3⃣
{
    x := x + d
    🚪 x
}
```

The mark is checked locally in each body, with no fixed point over member calls.
It is also what the symbol table records as "assigns a field".

## read and print

**Rule.** `📥` is a factor of type `i32` that reads one integer from standard
input with `scanf("%d")`. It goes wherever an expression does, including an
object's value list.

**Rule.** `📢` is a statement with one or more comma-separated values, printed
back to back with no separator and followed by exactly one newline. An `i32`
prints with `%d`, an `i64` with `%lld`, a `bool` as `true`/`false`, a string
literal as is (through `%s`, so `%` is safe), and an object as its type name and
fields in order, recursively:

```
📢 "sum: ", s, " ok: ", s == 9      sum: 9 ok: true
📢 c                                 Circle { center: Point { x: 3, y: 4 }, r: 5 }
```

The object print walks the field table, with one gep, load and printf per scalar
field, so a new structure needs no new compiler code.

## Name lookup

**Rule.** Variables and functions are separate namespaces: a name followed by `(`
is looked up among functions, any other name among variables.

**Rule.** Inside a member function, a variable name is looked up among the
parameters and locals (innermost block first), then the fields of the
structure, then the globals declared above the structure. The first match wins.
Inside a plain function the same search skips the fields. A bare call inside a
member looks at the structure's members first, then at the top-level functions.

**Rule.** Parameters share the body's outermost frame, so a local declared in the
body with a parameter's name is "already declared in this block". Shadowing a
field or a global is allowed: that is the order above.

A global is a variable or object declared at the outermost level of the
program. A variable inside a top-level `🤔`/`🔁` block is a local of `main` and
no function sees it.

## Code generation

- Every function and member is declared in the module before any body is
  generated, so a call to a function defined later resolves.
- Names carry a prefix that an ident cannot spell: `kg.add` for a function and
  `kg.Point.d2` for a member, whose first parameter is `%Point*`. This avoids a
  clash with `main`, `printf` and `scanf`, and between `Point_d2` and
  `Point.d2`.
- Each structure is an identified struct type, an object is an alloca of it, and
  a field is a gep by the index from the symbol table.
- Globals become `ir.GlobalVariable`s named `kg$x`, zero-initialised and stored when `main`
  reaches their decl. **The cost:** a function declared below a global but
  called by a statement above it reads that global as 0. This is accepted and
  documented rather than tracked.
- `printf` and `scanf` are bodiless variadic declarations, and every format
  string is a private global constant.
