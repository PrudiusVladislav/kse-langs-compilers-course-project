import sys

from llvmlite import ir
import llvmlite.binding as llvm

from lexer import CompileError, lex

I64, I32, I8, I1 = ir.IntType(64), ir.IntType(32), ir.IntType(8), ir.IntType(1)
FORMAT_INT = b"Program exit with result %lld\n\0"
FORMAT_TRUE = b"Program exit with result true\n\0"
FORMAT_FALSE = b"Program exit with result false\n\0"
IR_TYPES = {"i32": I32, "i64": I64, "bool": I1}
I32_MAX, I64_MAX = 2**31 - 1, 2**63 - 1
I32_MIN, I64_MIN = -(2**31), -(2**63)

TYPES = {"kw_i32": "i32", "kw_i64": "i64", "kw_bool": "bool"}
BOOLS = {"kw_true": True, "kw_false": False}
CMP_OPS = {"eq": "==", "ne": "!="}
ADD_OPS = {"plus": "+", "minus": "-"}
MUL_OPS = {"star": "*"}


def wider(a, b):
    return "i64" if "i64" in (a, b) else a


def literal_value(expr):
    """The constant an expression is, after negation folding, or None."""
    if isinstance(expr, ConstNode):
        return expr.value
    return getattr(expr, "folded", None)


class Node:
    def __init__(self, line, col):
        self.line = line
        self.col = col
        self.type = None

    def children(self):
        return []

    def dump(self, depth=0):
        lines = ["  " * depth + self.label()]
        for child in self.children():
            lines.extend(child.dump(depth + 1))
        return lines


class ProgramNode(Node):
    def __init__(self, line, col, statements, exit):
        super().__init__(line, col)
        self.statements = statements
        self.exit = exit

    def label(self):
        return "Program"

    def children(self):
        return self.statements + [self.exit]

    def accept(self, visitor):
        return visitor.visit_program(self)


class StmtNode(Node):
    pass


class DeclNode(StmtNode):
    def __init__(self, line, col, name, type_name, mutable, init):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable
        self.init = init

    def label(self):
        return f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return [self.init]

    def accept(self, visitor):
        return visitor.visit_decl(self)


class AssignNode(StmtNode):
    def __init__(self, line, col, name, value):
        super().__init__(line, col)
        self.name = name
        self.value = value

    def label(self):
        return f"Assign {self.name}"

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_assign(self)


class IfNode(StmtNode):
    def __init__(self, line, col, cond, then_block, else_block):
        super().__init__(line, col)
        self.cond = cond
        self.then_block = then_block
        self.else_block = else_block

    def label(self):
        return "If"

    def children(self):
        return [self.cond, self.then_block] + ([self.else_block] if self.else_block else [])

    def accept(self, visitor):
        return visitor.visit_if(self)


class WhileNode(StmtNode):
    def __init__(self, line, col, cond, body):
        super().__init__(line, col)
        self.cond = cond
        self.body = body

    def label(self):
        return "While"

    def children(self):
        return [self.cond, self.body]

    def accept(self, visitor):
        return visitor.visit_while(self)


class ExitNode(Node):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return "Exit"

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_exit(self)


class BlockNode(Node):
    def __init__(self, line, col, statements, exit):
        super().__init__(line, col)
        self.statements = statements
        self.exit = exit

    def label(self):
        return "Block"

    def children(self):
        return self.statements + ([self.exit] if self.exit else [])

    def accept(self, visitor):
        return visitor.visit_block(self)


class ExprNode(Node):
    pass


class BinOpNode(ExprNode):
    def __init__(self, line, col, op, left, right):
        super().__init__(line, col)
        self.op = op
        self.left = left
        self.right = right

    def label(self):
        return f"BinOp {self.op}"

    def children(self):
        return [self.left, self.right]

    def accept(self, visitor):
        return visitor.visit_binop(self)


class NotNode(ExprNode):
    def __init__(self, line, col, operand):
        super().__init__(line, col)
        self.operand = operand

    def label(self):
        return "Not"

    def children(self):
        return [self.operand]

    def accept(self, visitor):
        return visitor.visit_not(self)


class NegNode(ExprNode):
    def __init__(self, line, col, operand):
        super().__init__(line, col)
        self.operand = operand

    def label(self):
        return "Neg"

    def children(self):
        return [self.operand]

    def accept(self, visitor):
        return visitor.visit_neg(self)


class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name

    def label(self):
        return f"Var {self.name}"

    def accept(self, visitor):
        return visitor.visit_var(self)


class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Const {self.value}"

    def accept(self, visitor):
        return visitor.visit_const(self)


class BoolNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Bool {'true' if self.value else 'false'}"

    def accept(self, visitor):
        return visitor.visit_bool(self)


UNARY_OPS = {"not": NotNode, "minus": NegNode}


class Parser:
    def __init__(self, lines):
        self.lines = [toks for toks in ([t for t in l if t.kind != "endline"] for l in lines) if toks]
        self.line_pos = 0
        self.toks = []
        self.pos = 0

    def peek_line(self):
        return self.lines[self.line_pos] if self.line_pos < len(self.lines) else None

    def next_line(self):
        self.toks, self.pos = self.lines[self.line_pos], 0
        self.line_pos += 1

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def eat(self):
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def error(self, message):
        tok = self.peek()
        if tok is not None:
            return CompileError(tok.line, tok.col, f"{message}, got '{tok.text}'")
        last = self.toks[-1]
        return CompileError(
            last.line, last.col + len(last.text), f"{message}, found end of line"
        )

    def end_line(self):
        if (tok := self.peek()) is not None:
            raise CompileError(tok.line, tok.col, f"unexpected '{tok.text}' after the statement")

    def expect(self, kind, what):
        if self.peek() is None or self.peek().kind != kind:
            raise self.error(f"expected {what}")
        return self.eat()

    def parse_factor(self):
        tok = self.peek()
        if tok is None:
            raise self.error("expected a constant or a variable")
        if tok.kind == "number":
            self.eat()
            return ConstNode(tok.line, tok.col, int(tok.text))
        if tok.kind in BOOLS:
            self.eat()
            return BoolNode(tok.line, tok.col, BOOLS[tok.kind])
        if tok.kind == "ident":
            self.eat()
            return VarNode(tok.line, tok.col, tok.text)
        if tok.kind in UNARY_OPS:
            self.eat()
            return UNARY_OPS[tok.kind](tok.line, tok.col, self.parse_factor())
        if tok.kind == "lparen":
            self.eat()
            node = self.parse_expr()
            self.expect("rparen", "')'")
            return node
        raise self.error("expected a constant or a variable")

    def parse_term(self):
        node = self.parse_factor()
        while (tok := self.peek()) is not None and tok.kind in MUL_OPS:
            self.eat()
            node = BinOpNode(tok.line, tok.col, MUL_OPS[tok.kind], node, self.parse_factor())
        return node

    def parse_arith(self):
        node = self.parse_term()
        while (tok := self.peek()) is not None and tok.kind in ADD_OPS:
            self.eat()
            node = BinOpNode(tok.line, tok.col, ADD_OPS[tok.kind], node, self.parse_term())
        return node

    def parse_expr(self):
        node = self.parse_arith()
        if (tok := self.peek()) is not None and tok.kind in CMP_OPS:
            self.eat()
            node = BinOpNode(tok.line, tok.col, CMP_OPS[tok.kind], node, self.parse_arith())
        return node

    def parse_decl(self):
        type_name = TYPES[self.eat().kind]
        mutable = self.peek() is not None and self.peek().kind == "kw_mut"
        if mutable:
            self.eat()
        name = self.expect("ident", "a variable name")
        if self.peek() is None or self.peek().kind != "langle":
            raise CompileError(
                name.line, name.col, f"variable '{name.text}' needs an initialiser in ⟨⟩"
            )
        self.eat()
        init = self.parse_expr()
        self.expect("rangle", "'⟩'")
        return DeclNode(name.line, name.col, name.text, type_name, mutable, init)

    def parse_assign(self):
        name = self.eat()
        self.expect("assign", f"':=' after '{name.text}'")
        return AssignNode(name.line, name.col, name.text, self.parse_expr())

    def parse_block(self, after):
        if self.peek_line() is not None:
            self.next_line()
        brace = self.expect("lbrace", f"'{{' on its own line after '{after}'")
        self.end_line()
        statements, exit_node = self.parse_body()
        toks = self.peek_line()
        if toks is None:
            raise CompileError(brace.line, brace.col, "'{' is never closed")
        if toks[0].kind != "rbrace":
            raise CompileError(
                toks[0].line, toks[0].col, "statement after '\U0001f6aa' in the same block"
            )
        if not statements and exit_node is None:
            raise CompileError(brace.line, brace.col, "empty block")
        self.next_line()
        self.eat()
        self.end_line()
        return BlockNode(brace.line, brace.col, statements, exit_node)

    def parse_if(self):
        tok = self.eat()
        cond = self.parse_expr()
        self.end_line()
        then_block = self.parse_block(tok.text)
        else_block = None
        if (toks := self.peek_line()) is not None and toks[0].kind == "kw_else":
            self.next_line()
            els = self.eat()
            self.end_line()
            else_block = self.parse_block(els.text)
        return IfNode(tok.line, tok.col, cond, then_block, else_block)

    def parse_while(self):
        tok = self.eat()
        cond = self.parse_expr()
        self.end_line()
        return WhileNode(tok.line, tok.col, cond, self.parse_block(tok.text))

    def parse_exit(self):
        tok = self.eat()
        return ExitNode(tok.line, tok.col, self.parse_expr())

    def parse_statement(self):
        tok = self.peek()
        if tok.kind in TYPES:
            return self.parse_decl()
        if tok.kind == "ident":
            return self.parse_assign()
        if tok.kind == "kw_if":
            return self.parse_if()
        if tok.kind == "kw_while":
            return self.parse_while()
        if tok.kind == "kw_else":
            raise CompileError(tok.line, tok.col, "'\U0001f643' without a '\U0001f914'")
        raise CompileError(tok.line, tok.col, f"'{tok.text}' does not start a statement")

    def parse_body(self):
        statements, exit_node = [], None
        while (toks := self.peek_line()) is not None and toks[0].kind not in ("kw_exit", "rbrace"):
            self.next_line()
            statements.append(self.parse_statement())
            self.end_line()
        if toks is not None and toks[0].kind == "kw_exit":
            self.next_line()
            exit_node = self.parse_exit()
            self.end_line()
        return statements, exit_node

    def parse_program(self):
        statements, exit_node = self.parse_body()
        if (toks := self.peek_line()) is not None:
            tok = toks[0]
            if tok.kind == "rbrace":
                raise CompileError(tok.line, tok.col, "'}' without a matching '{'")
            raise CompileError(tok.line, tok.col, "'\U0001f6aa' must be the last statement")

        if exit_node is None:
            if not statements:
                raise CompileError(1, 1, "the program is empty: it must end with '\U0001f6aa'")
            last = statements[-1]
            raise CompileError(last.line, last.col, "the program must end with '\U0001f6aa'")
        return ProgramNode(1, 1, statements, exit_node)


class SemanticChecker:
    def __init__(self):
        self.scopes = [{}]

    def lookup(self, node):
        for frame in reversed(self.scopes):
            if node.name in frame:
                return frame[node.name]
        raise CompileError(
            node.line, node.col, f"variable '{node.name}' is used before its declaration"
        )

    def check_condition(self, node, keyword):
        if (have := node.cond.accept(self)) != "bool":
            raise CompileError(
                node.line, node.col, f"the condition of '{keyword}' must be bool, got {have}"
            )

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return
        if (value := literal_value(expr)) is not None and have == "i64" and want == "i32":
            raise CompileError(expr.line, expr.col, f"constant {value} does not fit in i32")
        raise CompileError(
            at.line, at.col, f"cannot {what} of type {want} with a value of type {have}"
        )

    def visit_program(self, node):
        for statement in node.statements:
            statement.accept(self)
        node.exit.accept(self)

    def visit_decl(self, node):
        if node.name in self.scopes[-1]:
            raise CompileError(
                node.line, node.col, f"variable '{node.name}' is already declared in this block"
            )
        node.init.accept(self)
        self.check_assignable(node.init, node.type_name, node, f"initialise '{node.name}'")
        self.scopes[-1][node.name] = node

    def visit_assign(self, node):
        decl = self.lookup(node)
        if not decl.mutable:
            raise CompileError(
                node.line, node.col, f"cannot assign to '{node.name}': it is not \U0001f513"
            )
        node.decl = decl
        node.value.accept(self)
        self.check_assignable(node.value, decl.type_name, node, f"assign to '{node.name}'")

    def visit_if(self, node):
        self.check_condition(node, "\U0001f914")
        node.then_block.accept(self)
        if node.else_block:
            node.else_block.accept(self)

    def visit_while(self, node):
        self.check_condition(node, "\U0001f501")
        node.body.accept(self)

    def visit_block(self, node):
        self.scopes.append({})
        for statement in node.statements:
            statement.accept(self)
        if node.exit:
            node.exit.accept(self)
        self.scopes.pop()

    def visit_exit(self, node):
        node.value.accept(self)

    def visit_binop(self, node):
        lt, rt = node.left.accept(self), node.right.accept(self)
        if node.op in ADD_OPS.values() or node.op in MUL_OPS.values():
            if "bool" in (lt, rt):
                raise CompileError(node.line, node.col, f"cannot apply '{node.op}' to bool")
            node.type = wider(lt, rt)
        else:
            if (lt == "bool") != (rt == "bool"):
                raise CompileError(node.line, node.col, f"cannot compare {lt} with {rt}")
            node.type = "bool"
        return node.type

    def visit_not(self, node):
        if (have := node.operand.accept(self)) != "bool":
            raise CompileError(node.line, node.col, f"cannot apply '!' to {have}")
        node.type = "bool"
        return node.type

    def visit_neg(self, node):
        if isinstance(node.operand, ConstNode):
            node.folded = -node.operand.value
            return self.range_check(node, node.folded)
        if (have := node.operand.accept(self)) == "bool":
            raise CompileError(node.line, node.col, "cannot apply '-' to bool")
        node.type = have
        return node.type

    def visit_var(self, node):
        node.decl = self.lookup(node)
        node.type = node.decl.type_name
        return node.type

    def visit_const(self, node):
        return self.range_check(node, node.value)

    def range_check(self, node, value):
        if not I64_MIN <= value <= I64_MAX:
            raise CompileError(node.line, node.col, f"constant {value} does not fit in i64")
        node.type = "i32" if I32_MIN <= value <= I32_MAX else "i64"
        return node.type

    def visit_bool(self, node):
        node.type = "bool"
        return node.type


class CodeGen:
    def __init__(self):
        self.module = ir.Module(name="kaguya")
        self.module.triple = llvm.get_default_triple()

        self.function = ir.Function(self.module, ir.FunctionType(I32, []), name="main")
        self.builder = ir.IRBuilder(self.function.append_basic_block("entry"))
        self.printf = ir.Function(
            self.module,
            ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True),
            name="printf",
        )

        self.slots = {}
        self.emit = {
            "+": self.builder.add,
            "-": self.builder.sub,
            "*": self.builder.mul,
        }

    def alloca_entry(self, type, name):
        with self.builder.goto_entry_block():
            return self.builder.alloca(type, name=name)

    def coerce(self, value, have, want):
        if have == "i32" and want == "i64":
            return self.builder.sext(value, I64, name="wide")
        return value

    def global_string(self, name, data):
        text = self.module.globals.get(name)
        if text is None:
            text_type = ir.ArrayType(I8, len(data))
            text = ir.GlobalVariable(self.module, text_type, name=name)
            text.linkage, text.global_constant = "private", True
            text.initializer = ir.Constant(text_type, bytearray(data))
        return text.bitcast(ir.PointerType(I8))

    def print_int(self, value):
        self.builder.call(self.printf, [self.global_string("fmt_int", FORMAT_INT), value])

    def print_bool(self, value):
        fmt = self.builder.select(
            value,
            self.global_string("fmt_true", FORMAT_TRUE),
            self.global_string("fmt_false", FORMAT_FALSE),
        )
        self.builder.call(self.printf, [fmt])

    def visit_program(self, node):
        for statement in node.statements:
            statement.accept(self)
        node.exit.accept(self)
        return self.module

    def visit_decl(self, node):
        value = self.coerce(node.init.accept(self), node.init.type, node.type_name)
        slot = self.alloca_entry(IR_TYPES[node.type_name], node.name)
        self.builder.store(value, slot)
        self.slots[node] = slot

    def visit_assign(self, node):
        value = self.coerce(node.value.accept(self), node.value.type, node.decl.type_name)
        self.builder.store(value, self.slots[node.decl])

    def branch_unless_terminated(self, target):
        if not self.builder.block.is_terminated:
            self.builder.branch(target)

    def visit_if(self, node):
        then_bb = self.function.append_basic_block("then")
        else_bb = self.function.append_basic_block("else") if node.else_block else None
        merge_bb = self.function.append_basic_block("merge")
        self.builder.cbranch(node.cond.accept(self), then_bb, else_bb or merge_bb)

        self.builder.position_at_end(then_bb)
        node.then_block.accept(self)
        self.branch_unless_terminated(merge_bb)
        if else_bb:
            self.builder.position_at_end(else_bb)
            node.else_block.accept(self)
            self.branch_unless_terminated(merge_bb)
        self.builder.position_at_end(merge_bb)

    def visit_while(self, node):
        cond_bb = self.function.append_basic_block("cond")
        body_bb = self.function.append_basic_block("body")
        end_bb = self.function.append_basic_block("end")
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        self.builder.cbranch(node.cond.accept(self), body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        node.body.accept(self)
        self.branch_unless_terminated(cond_bb)
        self.builder.position_at_end(end_bb)

    def visit_block(self, node):
        for statement in node.statements:
            statement.accept(self)
        if node.exit:
            node.exit.accept(self)

    def visit_exit(self, node):
        value = node.value.accept(self)
        if node.value.type == "bool":
            self.print_bool(value)
        else:
            self.print_int(self.coerce(value, node.value.type, "i64"))
        self.builder.ret(ir.Constant(I32, 0))

    def visit_binop(self, node):
        want = wider(node.left.type, node.right.type)
        left = self.coerce(node.left.accept(self), node.left.type, want)
        right = self.coerce(node.right.accept(self), node.right.type, want)
        if node.op in "+-*":
            return self.emit[node.op](left, right)
        return self.builder.icmp_signed(node.op, left, right)

    def visit_not(self, node):
        return self.builder.xor(node.operand.accept(self), ir.Constant(I1, True))

    def visit_neg(self, node):
        if hasattr(node, "folded"):
            return ir.Constant(IR_TYPES[node.type], node.folded)
        return self.builder.neg(self.coerce(node.operand.accept(self), node.operand.type, node.type))

    def visit_var(self, node):
        return self.builder.load(self.slots[node.decl])

    def visit_const(self, node):
        return ir.Constant(IR_TYPES[node.type], node.value)

    def visit_bool(self, node):
        return ir.Constant(I1, node.value)


def main(argv):
    ast_only = len(argv) > 1 and argv[1] == "--ast"
    args = argv[2:] if ast_only else argv[1:]
    if len(args) != (1 if ast_only else 2):
        print(
            f"usage: {argv[0]} <input> <output.ll>\n"
            f"       {argv[0]} --ast <input>",
            file=sys.stderr,
        )
        return 2

    try:
        with open(args[0], "rb") as f:
            source = f.read()
    except OSError as exc:
        print(f"cannot read {args[0]}: {exc}", file=sys.stderr)
        return 2

    try:
        program = Parser(lex(source)).parse_program()
        if ast_only:
            print("\n".join(program.dump()))
            return 0
        program.accept(SemanticChecker())
        module = program.accept(CodeGen())
    except CompileError as exc:
        print(f"compilation error: line {exc.line}:{exc.col}: {exc.message}", file=sys.stderr)
        return 1

    with open(args[1], "w") as f:
        f.write(str(module))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
