# Kaguya — stage 1: language design, lexer and parser

A compiler for a small language with emoji keywords. `lexer.py` turns source
bytes into typed tokens with a hand-written state machine; `Parser` in
`compiler.py` reads those tokens by recursive descent and builds a tree;
`SemanticChecker` walks the tree, decides the type of every expression and
rejects bad programs; `CodeGen` walks the checked tree and emits LLVM IR through
the `llvmlite.ir` builder. `grammar.ebnf` is the grammar the parser implements,
one method per rule, and `design.md` records why the language looks the way it
does.

## The glyphs

| glyph | means | bytes |
|-------|-------|-------|
| `3⃣` | `i32` | `33 E2 83 A3` |
| `6⃣` | `i64` | `36 E2 83 A3` |
| `❓` | `bool` | `E2 9D 93` |
| `🔓` | mutable | `F0 9F 94 93` |
| `🤔` | `if` | `F0 9F A4 94` |
| `🙃` | `else` | `F0 9F 99 83` |
| `🔁` | `while` | `F0 9F 94 81` |
| `🚪` | `exit` | `F0 9F 9A AA` |
| `✅` / `❌` | `true` / `false` | `E2 9C 85` / `E2 9D 8C` |
| `⟨` `⟩` | initialiser | `E2 9F A8` / `E2 9F A9` |
| `≠` | not equal | `E2 89 A0` |

No keyword is spelled with letters. An identifier is always an identifier, so
the lexer's `IDENT` state has no keyword table behind it.

**Type the glyphs without a variation selector.** Editors and phone keyboards
often insert U+FE0F after an emoji, which turns `3⃣` from four bytes into
seven and will not lex. `port_corpus.py` refuses to write a file containing one.

## Running

```sh
python3 compiler.py input.txt output.ll   # compile
python3 compiler.py --ast input.txt       # print the tree, write nothing
lli output.ll                             # run
opt -passes=mem2reg -S output.ll          # the same IR, allocas promoted
```

Or through the full toolchain:

```sh
llc -filetype=obj -relocation-model=pic output.ll -o output.o
clang -fPIE output.o -o program && ./program
```

The lexer can be inspected on its own:

```sh
python3 lexer.py lexer_demo.txt
```

Every one of these has a `make` target: `make run`, `make ast`, `make build`,
`make phi`, `make tokens`, `make tests`, `make check`.

Errors go to stderr as one line with a 1-based `line:column`, and nothing is
written to the output file:

```
compilation error: line 2:5: cannot initialise 'c' of type i32 with a value of type i64
```

When a line ends too early the column is the one right after its last token —
`x := x +` reports `2:9`, the place where something should have been typed.

The column counts characters, not bytes, so it matches what an editor's cursor
shows even on a line full of four-byte glyphs.

## The phases

```
lex      bytes  -> one token vector per line        lexer.py
parse    tokens -> a tree of Node subclasses        Parser
check    tree   -> the same tree, typed             SemanticChecker
walk     tree   -> LLVM IR                          CodeGen
```

A statement used to be a line. An `if` is several, so the parser has a line
cursor beside its token cursor: `parse_statement` consumes one line, or — for an
`🤔` — the condition line, the `{` line, every line of the block and the `}`
line. Braces moved with it. The Practice 2 rule that `{` must close on its line
is gone from the lexer; `{` and `}` are plain tokens and the parser pairs them,
which is what lets a block span lines. `3⃣ x ⟨ 5` is still an error at the same
place, now `1:8: expected '}', found end of line`.

The parser is recursive descent, written by hand: `peek`, `eat`, `expect`, and
one method per grammar rule. One token of look-ahead decides every choice. The
tree holds names, numbers, flags and child nodes — never a token — so every node
carries the `line, col` of the token it came from, and later passes report their
errors from there.

Precedence and associativity come out of the grammar rather than a table:
`parse_arith` calls `parse_term` before it looks for `+`, so everything a `*`
glues together is already one node; the loop builds each new `BinOpNode` with
what it has so far as the left child, which is left associativity. `parse_expr`
wraps one `arith` and at most one comparison, so `==` and `!=` bind weakest.

```
3⃣ x ⟨ 2 + 3 * 4 ⟩        14, not 20
3⃣ 🔓 a ⟨ 10 - 3 - 2 ⟩   5, not 9
❓ c ⟨ x * 2 ≠ a + 5 ⟩  compares 28 with 10
```

The semantic pass owns the symbol table (name → declaration) and runs on the
whole tree before `CodeGen` is constructed, so a rejected program never has a
single instruction built. Every expression gets `node.type`, every name gets
`node.decl`; the generator reads those two fields and looks nothing up. `--ast`
stops after parsing, so it prints any well-formed tree, typed correctly or not.

## The language

One statement per line.

```
3⃣ x ⟨ 0 ⟩                  declaration, const, initialiser mandatory
6⃣ 🔓 y ⟨ 10 ⟩             declaration, mutable
❓ b ⟨ x == y ⟩            == and ≠ give a bool; one comparison per expression
6⃣ z ⟨ 2 + 3 * y ⟩          any chain of +, - and * over constants and variables
3⃣ w ⟨ (2 + 3) * 4 ⟩        parentheses group
3⃣ n ⟨ -w ⟩                 unary minus
y := x * 2 - 1              assignment; only a 🔓 variable may be assigned
❓ c ⟨ !b ⟩                ! negates a bool
🚪 y                      prints "Program exit with result <value>"
```

An initialiser is delimited by `⟨ ⟩`; `{` and `}` mean a block and nothing
else. One symbol, one job — so the parser never needs lookahead to tell the two
apart, and a brace on a declaration line is an ordinary error.

Mutability is const by default: `🔓` is optional and follows the type, which
keeps a declaration line starting with its type token.

An `🤔` and its condition stand on one line; `{`, `}` and `🙃` stand alone on
theirs; `🙃` is optional; a block holds at least one line. Blocks nest, since
an `🤔` is a statement. A block may end with `🚪` as its last line, and nothing
follows an `🚪` inside the same block.

```
3⃣ 🔓 a ⟨ 10 ⟩
❓ b ⟨ ✅ ⟩
🤔 b
{
    a := a + 5
}
🙃
{
    a := a - 5
}
🚪 a
```

`🔁` takes the same block:

```
3⃣ 🔓 i ⟨ 1 ⟩
3⃣ 🔓 sum ⟨ 0 ⟩
🔁 i ≠ 11
{
    sum := sum + i
    i := i + 1
}
🚪 sum
```

## Precedence

Precedence comes out of the grammar rather than a table. `parse_arith` calls
`parse_term` before it looks for `+`, so everything a `*` glues together is
already one node; the loop builds each new node with what it has so far as the
left child, which is left associativity. `parse_expr` wraps one `arith` and at
most one comparison, so `==` and `≠` bind weakest.

`!` and unary `-` sit in `factor` and recurse on `factor`, so they bind tighter
than every binary operator, and `( expr )` is the only way to override what
`arith` and `term` fix.

```
3⃣ x ⟨ 2 + 3 * 4 ⟩          14, not 20
3⃣ y ⟨ (2 + 3) * 4 ⟩        20
3⃣ 🔓 a ⟨ 10 - 3 - 2 ⟩     5, not 9
3⃣ z ⟨ -x * 2 ⟩             (-x) * 2, not -(x * 2)
❓ c ⟨ !(x == y) ⟩         parens let ! take a comparison
```

Parentheses build no node of their own: `(2 + 3) * 4` dumps as a plain
multiplication over an addition, so the tree shows the grouping without a
wrapper for it.

## The type rules

- A constant has the narrowest type it fits: `10` is `i32`, `3000000000` is
  `i64`, anything above `2⁶³−1` is an error.
- `+ - *` take two integers; the result is the wider type.
- `==` and `≠` take two integers of any widths or two bools; the result is `bool`.
- The one conversion is `i32 → i64`, in an initialiser or an assignment. Nothing
  narrows, and a bool never meets an integer.
- The condition of an `🤔` or a `🔁` is a bool, and `!` takes a bool. Unary `-` takes an integer.

`CodeGen` turns every widening the checker allowed into an explicit `sext` via
one `coerce` helper, called from the initialiser, the assignment, both operands
of `+ - *`, both operands of `==` and `≠`, and the exit value. Comparisons are
`icmp` on operands of equal width; integers are printed as `i64` with `%lld`,
bools by a `select` between two complete format strings.

## Scopes are not basic blocks

A block is one rule in the grammar, one frame in the semantic pass and one
builder position in the generator, and those three do not line up. The symbol
table is a stack of frames: a block pushes one on entry and pops it on exit, a
declaration goes into the top frame, and only the top frame is checked for a
duplicate — so an outer name may be declared again inside, with any type. A use
walks the frames from the top down and takes the first hit; a name whose frame
has been popped is "used before its declaration" like any other unknown name.

```
3⃣ 🔓 x ⟨ 10 ⟩
🤔 ✅
{
    ❓ 🔓 x ⟨ ✅ ⟩
    🤔 x
    {
        6⃣ 🔓 x ⟨ 20 ⟩
        🚪 x
    }
    🚪 x
}
🚪 x
```

Three `x`, three declarations, three types, three slots. The `exit` in the inner
block prints 20; the one after it would read the `bool`, and the last line would
read the `i32`, but neither is reached. Three frames are live at the deepest
point — the global one the checker starts with and one per block — while the IR
needs five basic blocks: `entry`, then a `then` and a `merge` for each `if`.
Neither `if` has an `else`, so neither gets one, and `cbranch` goes straight to
`merge`.

The counts differ because frames follow braces and basic blocks follow jumps.
`merge` belongs to no brace, and an `exit` terminates a basic block without
popping a frame — the `🚪 x` after the inner `🤔` is in the outer block's frame
but in the inner `if`'s `merge` block. Here both merges end up with no
predecessor, since the arms before them return; a merge with no predecessor is
still valid IR.

`tests/ok/scope_shadow-type.txt` is that program, and this is what it compiles
to — three slots in `entry`, one per declaration, and five blocks each ending
once:

```
entry:     %x = alloca i32 · %x.1 = alloca i1 · %x.2 = alloca i64   br i1 true
then:      stores the bool x, loads it as the inner condition        br i1
merge:     reads the i32 x — the last line                           ret
then.1:    reads the i64 x, prints 20                                ret
merge.1:   reads the i1 x through a select                           ret
```

What keeps the two apart in the code is `node.decl`. The semantic pass resolves
every `Var` and every `Assign` to the declaration it means, and the generator
reads that field — it never looks a name up, so it never has to know which `x` is
which.

## Where the allocas go

Every `alloca` goes to the entry block whatever block the builder is in when the
declaration is met, through one helper that positions at entry, allocates and
comes back. Two reasons: a slot created inside `then` does not exist on the
`else` path, and LLVM only promotes entry-block allocas to registers. Shadowing
costs nothing — two declarations, two slots.

That promotion is worth looking at. For a program that assigns the same variable
in both arms, `opt -passes=mem2reg -S` drops both stores and starts the merge
block with a `phi`:

```
merge:                              ; preds = %else, %then
  %r.0 = phi i32 [ 1, %then ], [ 2, %else ]
```

A value that depends on which block control came from. That is SSA, and the
allocas are what let LLVM build it. `tests/ok/if-else_both-arms-assign.txt` is
the program; `make phi` is the demo.

## Deliberate decisions

**The keycap collision.** `3⃣` is `U+0033 U+20E3` — the ASCII byte `3` followed
by a combining enclosing keycap. In `START` the byte `3` is therefore ambiguous
between a number literal and a type keyword, and the state machine cannot decide
until it has seen three more bytes. `NUMBER` resolves it by peeking two bytes on
`0xE2`: with `83 A3` and a run of exactly `3` or `6` it emits the type, with
`83 A3` and a longer run it reports a keycap on a multi-digit number, and
otherwise the run was a number and the `0xE2` is reported where it stands. A
digit straight after a type glyph is an error too. See
`tests/err/keycap_multidigit.txt` and `tests/err/keycap_then_number.txt`.

**Columns count characters, not bytes.** `col` advances once per decoded
character: the main loop increments it only on a UTF-8 lead byte. With a 4-byte
glyph on every line, byte columns would put every message several positions past
where an editor's cursor sits. `tests/err/emoji_column.txt` is the regression
test — the offending byte is at character 16 and byte 23.

**Unary minus and the width boundaries.** `-2147483648` parses as a negation of
`2147483648`, and that constant alone is already too wide for an `i32` — so a
literal-minded checker would reject the exact minimum of the type. The checker
folds a negation of a constant into a negative constant *before* the range
check, and ranges on signed bounds, so both type minimums are legal and
`-2147483649` is not. The fold lives in the semantic pass, not the parser:
`--ast` still shows the negation over a positive constant, so the tree mirrors
the source. See `tests/ok/neg_i32_min.txt` and `tests/err/neg_i32_overflow.txt`.

**`≠` is stored as `!=`.** The source glyph is `≠`, but the operator string
kept on the node is the ASCII `!=`, because `llvmlite`'s `icmp_signed` takes an
LLVM predicate and rejects anything else. `--ast` therefore prints `BinOp !=` —
the one place the tree does not echo the source. A literal `!=` in source is now
`!` followed by a stray `=`, which the `=` error catches
(`tests/err/neq_ascii.txt`).

**Self-reference in an initialiser.** `3⃣ x ⟨ x ⟩` is rejected with
`variable 'x' is used before its declaration`. The name is entered into the symbol
table only *after* its initialiser has been checked, so a variable can never be
read from its own initialiser — the error falls out of the declaration-before-use
rule instead of needing a check of its own. See `tests/err/self_init.txt`.

**Where an oversized constant is reported.** A bare constant that is too big for
an `i32` target — `3⃣ x ⟨ 3000000000 ⟩` or `x := 3000000000` — is reported at
the constant: `constant 3000000000 does not fit in i32`. Inside an operation it is
the operation's type that does not fit, so `3⃣ x ⟨ a + 3000000000 ⟩` gets the
usual `cannot initialise 'x' of type i32 with a value of type i64` at the name
(`tests/err/const_in_arith.txt`).

## Tests

```sh
./run_tests.sh
```

Each `NAME.txt` is paired with `NAME.expected`. A program in `tests/ok/` is
compiled and run through `lli`, and its stdout is compared; a program in
`tests/err/` must fail with the expected message and leave no output file. Where
a `tests/ok/NAME.ast` exists, the `--ast` dump is compared against it too.

Everything needed is in the `Dockerfile`:

```sh
docker build -t lcd-stage1 .
docker run --rm -v "$PWD:/work" lcd-stage1 ./run_tests.sh
```

`check.py` runs the same programs and prints one row per test with a pass count
at the end. `run_tests.sh` is the stricter of the two: it also diffs the `.ast`
files.

## Layout

```
lexer.py        byte-by-byte state machine over UTF-8, incl. the keycap lookahead
grammar.ebnf    the grammar, one rule per parse method
design.md       the language design and why each choice was made
compiler.py     the AST classes, the parser, the semantic pass, the codegen walk
tests/ok/       40 programs that run, 15 of them with expected trees
tests/err/      53 programs that must fail
run_tests.sh    compiles and runs each test, compares against .expected
check.py        the same tests as a pass/fail table
port_corpus.py  the one-off script that ported the corpus to this syntax
Dockerfile      ubuntu:24.04 with llvm, clang and llvmlite
```
