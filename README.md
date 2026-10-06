# Practice 5 — if/else, scopes and basic blocks

A compiler for a small language of `i32`, `i64` and `bool`. `lexer.py` turns
source bytes into typed tokens; `Parser` in `compiler.py` reads those tokens and
builds a tree; `SemanticChecker` walks the tree, decides the type of every
expression and rejects bad programs; `CodeGen` walks the checked tree and emits
LLVM IR through the `llvmlite.ir` builder. `grammar.ebnf` is the grammar the
parser implements, one method per rule.

This practice adds the first branch. A block brings two things with it that are
easy to confuse: a scope in the semantic pass and a basic block in the IR.
Keeping them apart is the week's point — see **Scopes are not basic blocks**.

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

## The phases

```
lex      bytes  -> one token vector per line        lexer.py
parse    tokens -> a tree of Node subclasses        Parser
check    tree   -> the same tree, typed             SemanticChecker
walk     tree   -> LLVM IR                          CodeGen
```

A statement used to be a line. An `if` is several, so the parser has a line
cursor beside its token cursor: `parse_statement` consumes one line, or — for an
`if` — the condition line, the `{` line, every line of the block and the `}`
line. Braces moved with it. The Practice 2 rule that `{` must close on its line
is gone from the lexer; `{` and `}` are plain tokens and the parser pairs them,
which is what lets a block span lines. `i32 x{5` is still an error at the same
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
i32 x{2 + 3 * 4}        14, not 20
i32 mut a{10 - 3 - 2}   5, not 9
bool c{x * 2 != a + 5}  compares 28 with 10
```

The semantic pass owns the symbol table (name → declaration) and runs on the
whole tree before `CodeGen` is constructed, so a rejected program never has a
single instruction built. Every expression gets `node.type`, every name gets
`node.decl`; the generator reads those two fields and looks nothing up. `--ast`
stops after parsing, so it prints any well-formed tree, typed correctly or not.

## The language

One statement per line.

```
i32 x{0}              declaration, const, initialiser mandatory
i64 mut y{10}         declaration, mutable
bool b{x == y}        == and != give a bool; one comparison per expression
i64 z{2 + 3 * y}      initialiser: any chain of +, - and * over constants and variables
y := x * 2 - 1        assignment; only a mut variable may be assigned
bool c{!b}            ! negates a bool
exit y                prints "Program exit with result <value>"
```

An `if` and its condition stand on one line; `{`, `}` and `else` stand alone on
theirs; `else` is optional; a block holds at least one line. Blocks nest, since
an `if` is a statement. A block may end with `exit` as its last line, and
nothing follows an `exit` inside the same block.

```
i32 mut a{10}
bool b{true}
if b
{
    a := a + 5
}
else
{
    a := a - 5
}
exit a
```

`!` applies to the factor right after it, so it binds tighter than every
operator: `!a == b` compares `!a` with `b`. Spaces after `!` are optional, and
`!=` still lexes as one token — after `!` the machine looks at one byte, `=`
makes `!=`, anything else makes `!` and is read again. There are still no
parentheses and no unary minus.

`while` takes the same block:

```
i32 mut i{1}
i32 mut sum{0}
while i != 11
{
    sum := sum + i
    i := i + 1
}
exit sum
```

`exit` takes a constant, `true`, `false` or a variable, never an operation; a
bool prints as `true` or `false`. A variable is declared once, before its first
use. `i32`, `i64`, `bool`, `mut`, `exit`, `true` and `false` are reserved. Blank
lines and extra spacing are ignored. There are no parentheses, and a single `=`
or `!` is a lexical error.

## The type rules

- A constant has the narrowest type it fits: `10` is `i32`, `3000000000` is
  `i64`, anything above `2⁶³−1` is an error.
- `+ - *` take two integers; the result is the wider type.
- `== !=` take two integers of any widths or two bools; the result is `bool`.
- The one conversion is `i32 → i64`, in an initialiser or an assignment. Nothing
  narrows, and a bool never meets an integer.
- The condition of an `if` or a `while` is a bool, and `!` takes a bool.

`CodeGen` turns every widening the checker allowed into an explicit `sext` via
one `coerce` helper, called from the initialiser, the assignment, both operands
of `+ - *`, both operands of `== !=`, and the exit value. Comparisons are
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
i32 mut x{10}
if true
{
    bool mut x{true}
    if x
    {
        i64 mut x{20}
        exit x
    }
    exit x
}
exit x
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
popping a frame — the `exit x` after the inner `if` is in the outer block's frame
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

**Self-reference in an initialiser.** `i32 x{x}` is rejected with
`variable 'x' is used before its declaration`. The name is entered into the symbol
table only *after* its initialiser has been checked, so a variable can never be
read from its own initialiser — the error falls out of the declaration-before-use
rule instead of needing a check of its own. See `tests/err/self_init.txt`.

**Negative numbers.** Number literals are unsigned: a `number` token is digits
only, so `-` is always the binary subtraction operator. `i32 x{-5}` is rejected
(`tests/err/negative_literal.txt`). Negative *values* are fully supported —
arithmetic is signed and `exit` prints negative results (`tests/ok/negative.txt`).

**A single `!` is no longer an error.** Through Practice 4 the lexer paired `!`
with `=` and rejected it alone. Now `!` is an operator of its own, so what was
`tests/err/single_bang.txt` is a valid program and the file is replaced by
`tests/err/not_on-int.txt`, which keeps the error path by applying `!` to an
integer.

**Where an oversized constant is reported.** A bare constant that is too big for
an `i32` target — `i32 x{3000000000}` or `x := 3000000000` — is reported at the
constant: `constant 3000000000 does not fit in i32`. Inside an operation it is
the operation's type that does not fit, so `i32 x{a + 3000000000}` gets the usual
`cannot initialise 'x' of type i32 with a value of type i64` at the name
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
docker build -t lcd-practice5 .
docker run --rm -v "$PWD:/work" lcd-practice5 ./run_tests.sh
```

`check.py` runs the same programs and prints one row per test with a pass count
at the end. `run_tests.sh` is the stricter of the two: it also diffs the `.ast`
files.

## Layout

```
lexer.py        byte-by-byte state machine: START, IDENT, NUMBER, PAIR, BANG
grammar.ebnf    the grammar, one rule per parse method
compiler.py     the AST classes, the parser, the semantic pass, the codegen walk
tests/ok/       27 programs that run, 10 of them with expected trees
tests/err/      44 programs that must fail
run_tests.sh    compiles and runs each test, compares against .expected
check.py        the same tests as a pass/fail table
Dockerfile      ubuntu:24.04 with llvm, clang and llvmlite
```
