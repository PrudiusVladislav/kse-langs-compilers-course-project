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

# Stage 3 — decisions to pick

Every decision below is a menu. ✔ marks my pick; you choose. Each rule comes
before its syntax. Nothing here breaks a Stage 2 program: every new construct
starts with a token that was an error before (`[`, `]`) or adds an optional
mark in a place that used to reject it.

Two facts about Kaguya shape every choice: it has no `<`, `>`, `/` or `%`, so
a program can only compare with `==` and `≠` and compute with `+ - *`; and
objects are never copied, so anything aggregate is passed by reference or not
at all.

## F. Which feature (Task 1, 5 pt)

| | Arrays ✔ | Coroutines | Inheritance | Overloading |
|---|---|---|---|---|
| lexer | `[` `]` as two `SINGLES` | 2 glyphs: coroutine, yield | 2 glyphs: extends, virtual | nothing |
| parser | `[N]` after a type; `[e]` as a chain link; reuse `parse_init` | coroutine header, yield stmt, resume call | `🧱 B ⬅ A`, mark on a member | nothing |
| checker | array type (elem, size), constant-index range check, `🔓` array params, "not a value" rules | yield only inside a coroutine, yield type, resume type, done state | merged field table, duplicate across base, override signature match, member lookup through base | functions keyed by name + params, ranked resolution with `i32→i64`, ambiguity error |
| codegen | `ArrayType` alloca/field, gep `[0, i]`, bounds check block calling `dprintf` + `exit`, array params as pointers | locals and resume index moved into a state struct, `switch` into the resume block, `slot_of` returns a gep instead of an alloca | vptr at index 0, one vtable global per structure, load-gep-load-call, bitcast `this` to base | mangled names `kg.f.i32.i64` |
| IR excerpt | `icmp ult` → `br` to an `oob` block, then the `gep` | the state struct and the `switch i32 %state` | the vtable constant and the indirect `call` | two definitions, two calls picking each |
| visibility in a real program | high: a board, a stock list, a score table | high: a generator or a task scheduler | medium: shapes, needs a base member calling a virtual one, since objects are never parameters | low: reads as convenience |
| cost | medium | high | high | low, and looks thin at the defence |

**Why arrays.** Every row of the handout's checklist is a test you can write
and see fail; it extends rules Kaguya already has (const by default, `🔓` on
every step of a chain, never copied) instead of adding a new model; and the
run-time bounds error is a demo in itself. Codegen already does the hard part:
allocas live in entry, chains already walk geps.

**Loops — too thin, honestly.** Of the five items in the row, three already
ship in Stage 2 and were built in class: `🔁` with a condition, the back edge
(`cond`/`body`/`end`), a loop inside a loop, a body that declares a variable.
What is left is `for` over a range and an optional `break` — a day's work, and
the IR excerpt would show a back edge Stage 2 already prints. The handout asks
for a feature "not built in class"; I would not bet 5 points on it.

## A. Arrays, one decision at a time

**A1. What an array holds.** Rule: an array is a fixed number of elements of
one built-in type, the number is part of the type and is at least 1.
- a. `i32`, `i64`, `bool` only ✔ — `3⃣[5] a ⟨1, 2, 3, 4, 5⟩`
- b. also structures — `Point[3] ps ⟨⟨0, 0⟩, ⟨1, 1⟩, ⟨2, 2⟩⟩`; print and
  init nest one level deeper, mutability chains grow an index step.

**A2. Where the size goes.** Rule: a declaration still starts with its type, so
`parse_statement` keeps dispatching on one token.
- a. after the type ✔ — `3⃣[5] 🔓 a ⟨0⟩`; the same spelling is the param type
- b. after the name, C style — `3⃣ 🔓 a[5] ⟨0⟩`; a param would read `3⃣ a[5]`
- c. a glyph — `📦 3⃣ 5 🔓 a ⟨0⟩`; one more token, nothing gained

**A3. The initialiser.** Rule: mandatory, as for every variable.
- a. exactly one value per element — `3⃣[3] a ⟨1, 2, 3⟩`
- b. exactly one per element, or a single value that fills all, evaluated once ✔
  — `3⃣[9] 🔓 board ⟨0⟩`; a fill is lowered to a loop, not N stores
- c. as b, but the single value is evaluated per element —
  `3⃣[5] a ⟨📥⟩` reads five numbers; neat, but surprising for every other value

**A4. Arrays inside structures.** Rule: a field may be an array, and
`obj.f[i]` is a chain like `obj.f.x`.
- a. no, variables and params only — the struct layout is untouched
- b. yes ✔ — `🧱 Board { 3⃣[9] 🔓 cells … }`, `b.cells[4] := 1`; the program
  needs a struct whose member function works on an array, and the chain code
  is shared

**A5. Indexing.** Rule: `a[e]` takes an `i32` or `i64` index, never a bool;
one dimension only; an index ends a chain link, so `b.cells[i]` is fine and
`a[i][j]` is a syntax error.
- a. one dimension ✔ — `b.cells[r * 3 + c]`
- b. arrays of arrays — `3⃣[3][3] g`; nested types in checker and print

**A6. Out of range.** Rule: a constant index (after the existing negation fold)
outside `0 … N-1` is a compile error at the index; any other index is checked
at run time, unsigned, so a negative one is caught too.
- a. run-time error on stdout through `printf`, exit 1 — the test script sees it
  without changes, but it mixes with program output
- b. run-time error on stderr through `dprintf(2, …)`, exit 1 ✔ —
  `runtime error: line 7:9: index 12 out of range for 'a' of size 9`; the
  test script gains `2>&1` and a `.code` file for the expected exit status
- c. clamp or wrap — not what the handout asks

**A7. Passing to a function.** Rule: an array is never copied; a parameter is a
reference to the caller's array.
- a. sized params ✔ — `🧩 sum(3⃣[5] a) → 3⃣`; the size must match, the
  constant-index check still works inside
- b. unsized `3⃣[] a` with a hidden length and a length operator `📏a` — one
  `sum` for any size; costs a hidden argument, a new glyph, and the
  compile-time check inside the function

**A8. Writing through a parameter.** Rule: a parameter is constant, as in
Stage 2. An array parameter may be marked `🔓`; then the function may assign
its elements and the argument must be `🔓` at every step, exactly like a
mutating member call. A scalar parameter with `🔓` stays an error.
- a. array params read-only — `🧩 wins(3⃣[9] c, 3⃣ p) → ❓`
- b. `🔓` allowed on array params ✔ — `🧩 clear(3⃣[9] 🔓 c) → 3⃣`

**A9. What an array is not.** Rule: not a value of `:=`, `==`, arithmetic, a
result or `🚪`, mirroring objects.
- a. and not printable either
- b. but `📢 a` prints `[1, 0, 2]` ✔ — one loop over the elements, same as the
  object walk

## O. The optimizer (Task 2, 4 pt)

**O1. What `--ast` shows.** Rule: `--ast` alone stays parse-only, so every
Stage 2 `.ast` file still matches.
- a. `--optimize --ast` = parse, check, optimize, dump ✔
- b. `--ast` always checks first — changes what a broken-but-parseable
  program prints today

**O2. What counts as reading a variable.** Rule: a read is a `Var` (or an
array index/element `Var`) anywhere in an expression: an initialiser, a
condition, an argument, an index, a print, an exit, a chain root. Reads are
matched by `node.decl`, never by name, so shadowing and scopes come free.
- a. any `Var` reads — `x := x + 1` keeps `x` alive forever
- b. a `Var` inside an assignment to the same variable does not count ✔ —
  a counter nobody prints disappears with its updates

**O3. Side effects.** Rule: an expression that calls a function or `📥` is
never dropped.
- a. a variable whose initialiser or any assignment calls or reads is kept
  whole ✔ — `3⃣ junk ⟨📥⟩` stays; simple and obviously safe
- b. drop the variable, keep the calls as call statements — `📥` cannot stand
  as a statement in Kaguya, so this needs a new node

**O4. What the unused-variable pass may remove.**
- a. scalars and arrays ✔ — an array only written to goes with its element
  assignments
- b. scalars only
- c. objects too — a member call is a use, so an unused object is rare, and
  its init may read

Parameters are never removed: that would change the signature.

**O5. Unused functions.** Rule: removing a body may leave another function
unused, so the pass repeats until it removes nothing.
- a. reachability from `main` over `node.func` edges ✔ — removes mutual
  recursion islands too; one test shows the cascade
- b. count call sites, ignoring a function's calls to itself, repeat

Members are included (a member nobody calls goes), structures are not removed.

**O6. Which shapes are inlined.** A function called at exactly one site, after
O5. Recursive — direct or through others — never.

| shape | inline? |
|---|---|
| straight-line body, one trailing `🚪` | yes |
| `🚪` inside an `🤔` or `🔁` | no ✔ (a done flag and a result slot would do it) |
| member function | no ✔ (fields by bare name would need rewriting to chains) |
| a param is an array | no ✔ (a param must alias, not copy) |
| call in a decl, assign, field assign, exit, print, call statement, `🤔` condition | yes, if no other call or `📥` is in the statement |
| call in a `🔁` condition | never: hoisting runs it once instead of every turn |

The "no other call" guard is what keeps hoisting from reordering side
effects: `g(f(1))` and `f(1) + 📥` are left alone.
- a. the table above ✔
- b. also early exits through a flag — doubles the inliner for 2 of 4 points

**O7. Renaming.** Rule: every param and local of the inlined body gets a name
an identifier cannot spell, so it can never meet a caller's name.
- a. `add.a`, `add.s` for locals, `add.0` for the result ✔ — `.` is not an
  identifier character and an identifier cannot start with a digit; nested
  inlining gives `g.add.s`
- b. a counter, `s.1` — unique, but the tree no longer says where it came from
- c. `_inl_s` — a user can spell it

**O8. The value where the call stood.** Rule: the result keeps the function's
result type, so `i32 → i64` widening still happens where it did.
- a. a result decl ✔ — `6⃣ add.0 ⟨a + b⟩`, the call becomes `Var add.0`
- b. substitute the exit expression directly — prettier, but a body leaving
  with an `i32` from an `i64` function would multiply in 32 bits at the caller

**O9. After the passes.** Rule: the optimizer keeps `decl` and `type` on every
node it creates; the checker does not run again.
- a. no re-check ✔ — a global read in the body keeps pointing at the global
  even where the caller shadows it, which a re-check by name would get wrong
- b. re-run the checker as a safety net — breaks on exactly that case

Order: inline → unused variables → unused functions, all three repeated
until a round changes nothing. Codegen creates every global slot before
`main`'s first statement, so a read hoisted above a global's line still
finds it (`global_read_zero` stays `0 7`).

## P. The real program (Task 3, 4 pt)

Constraint: only `==`, `≠`, `+ - *`. Each idea needs nothing else.

1. **Tic-tac-toe** ✔ — `Board` with `3⃣[9] 🔓 cells`, member
   `🔓 place(3⃣ at, 3⃣ who) → ❓` (false if taken), function
   `wins(3⃣[9] c, 3⃣ p) → ❓` over the eight lines with constant indices,
   `📥` moves, the board printed after each turn. Sessions: X wins, a draw,
   and a third with move 12 that ends in the run-time bounds error.
2. **Vending machine** — `Machine` with `stock` and `price` arrays, member
   `🔓 sell(3⃣ slot) → ❓`, function `total(3⃣[5] p, 3⃣[5] s)`; reads slots
   until 0, prints a receipt. Stock is sold one unit at a time so `≠ 0` is
   enough.
3. **Polynomial** — `Poly` with `3⃣[5] c` read from input, member `at(3⃣ x)`
   by Horner, function `derive(3⃣[5] 🔓 out, 3⃣[5] in)`; prints p(x) and p'(x)
   for each x read.
4. **Memory pairs** — a fixed hidden `3⃣[8]` of pairs, `Game` member
   `🔓 open(3⃣ i, 3⃣ j) → ❓`, function `left(3⃣[8] shown)`; the player reads two
   positions per turn until all pairs are open.

## Plan

You:
- pick F, A1–A9, O1–O9 and P
- write `ai_usage_3.txt` (I can draft what was taken and changed; the
  "what I understood" part has to be yours)
- open or update the PR naming the feature; slides by 10 Nov 12:00

Me, in this order, one commit per step, on `stage3`:
1. `programs/` skeleton of the chosen program, smallest form that runs
2. lexer `[` `]`, grammar, parser, `--ast`
3. checker rules + 3 ok / 3 err tests per rule group, named `array_*`
4. codegen: types, gep, bounds check, params, print; IR excerpt into README
5. grow the program; two or three recorded sessions
6. optimizer: `--optimize` flag, three passes, `tests/opt/` with 6+ programs,
   `.ast` and `.expected` each; `run_tests.sh` runs the whole suite twice,
   with and without the flag
7. README: rules with examples and tests, IR excerpts (array, inlining
   before/after), build commands, ten lines on the program; Makefile `IMAGE`
