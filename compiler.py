import copy
import sys
from collections import Counter

from llvmlite import ir
import llvmlite.binding as llvm

from lexer import CompileError, lex

I64, I32, I8, I1 = ir.IntType(64), ir.IntType(32), ir.IntType(8), ir.IntType(1)
FORMAT_INT = b"Program exit with result %lld\n\0"
FORMAT_TRUE = b"Program exit with result true\n\0"
FORMAT_FALSE = b"Program exit with result false\n\0"
FORMAT_D, FORMAT_LLD, FORMAT_S = b"%d\0", b"%lld\0", b"%s\0"
IR_TYPES = {"i32": I32, "i64": I64, "bool": I1}
I32_MAX, I64_MAX = 2**31 - 1, 2**63 - 1
I32_MIN, I64_MIN = -(2**31), -(2**63)

TYPES = {"kw_i32": "i32", "kw_i64": "i64", "kw_bool": "bool"}
BOOLS = {"kw_true": True, "kw_false": False}
CMP_OPS = {"eq": "==", "ne": "!=", "lt": "<", "gt": ">"}
ORDER_OPS = {"<", ">"}
ADD_OPS = {"plus": "+", "minus": "-"}
MUL_OPS = {"star": "*"}
ARITH = {"+": "add", "-": "sub", "*": "mul"}


def wider(a, b):
    return "i64" if "i64" in (a, b) else a


def is_scalar(type_name):
    return isinstance(type_name, str) and type_name in IR_TYPES


def start(expr):
    return getattr(expr, "start", (expr.line, expr.col))


def walk(node):
    yield node
    for child in node.children():
        yield from walk(child)


def reads(node, target=None):
    if isinstance(node, VarNode) and node.decl is not target:
        yield node.decl
    if isinstance(node, AssignNode):
        target = node.decl
    for child in node.children():
        yield from reads(child, target)


def has_effect(node):
    return any(isinstance(n, (CallNode, ReadNode)) for n in walk(node))


def own_nodes(statement):
    if isinstance(statement, (IfNode, WhileNode)):
        return list(walk(statement.cond))
    if isinstance(statement, (FuncNode, StructNode)):
        return []
    return list(walk(statement))


def callees(func):
    seen, pending = set(), [func]
    while pending:
        for node in walk(pending.pop().body):
            if isinstance(node, CallNode) and node.func not in seen:
                seen.add(node.func)
                pending.append(node.func)
    return seen


def replace(root, old, new):
    for node in walk(root):
        for key, value in vars(node).items():
            if value is old:
                setattr(node, key, new)
            elif isinstance(value, list) and old in value:
                value[value.index(old)] = new


def aggregate(type_name):
    return "arrays" if isinstance(type_name, ArrayType) else "objects"


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


class ReadNode(ExprNode):
    def label(self):
        return "Read"

    def accept(self, visitor):
        return visitor.visit_read(self)


class StringNode(ExprNode):
    def __init__(self, line, col, text):
        super().__init__(line, col)
        self.text = text

    def label(self):
        return f"String {self.text}"


class CallNode(ExprNode):
    def __init__(self, line, col, name, args, target):
        super().__init__(line, col)
        self.name = name
        self.args = args
        self.target = target

    def label(self):
        kind = "MemberCall" if self.target else "Call"
        return f"{kind} {self.name} args={len(self.args)}"

    def children(self):
        return ([self.target] if self.target else []) + self.args

    def accept(self, visitor):
        return visitor.visit_call(self)


class DotNode(ExprNode):
    def __init__(self, line, col, target, name):
        super().__init__(line, col)
        self.target = target
        self.name = name

    def label(self):
        return f"Dot {self.name}"

    def children(self):
        return [self.target]

    def accept(self, visitor):
        return visitor.visit_dot(self)


class FieldAssignNode(StmtNode):
    def __init__(self, line, col, target, value):
        super().__init__(line, col)
        self.target = target
        self.value = value

    def label(self):
        return "FieldAssign"

    def children(self):
        return [self.target, self.value]

    def accept(self, visitor):
        return visitor.visit_field_assign(self)


class PrintNode(StmtNode):
    def __init__(self, line, col, values):
        super().__init__(line, col)
        self.values = values

    def label(self):
        return "Print"

    def children(self):
        return self.values

    def accept(self, visitor):
        return visitor.visit_print(self)


class IndexNode(ExprNode):
    def __init__(self, line, col, target, index):
        super().__init__(line, col)
        self.target = target
        self.index = index
        self.name = target.name

    def label(self):
        return f"Index {self.name}"

    def children(self):
        return [self.target, self.index]

    def accept(self, visitor):
        return visitor.visit_index(self)


class IndexAssignNode(StmtNode):
    def __init__(self, line, col, target, value):
        super().__init__(line, col)
        self.target = target
        self.value = value

    def label(self):
        return "IndexAssign"

    def children(self):
        return [self.target, self.value]

    def accept(self, visitor):
        return visitor.visit_index_assign(self)


class ParamNode(Node):
    def __init__(self, line, col, name, type_name, mutable):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable

    def label(self):
        return f"Param {self.name} {self.type_name}{' mut' if self.mutable else ''}"


class FuncNode(StmtNode):
    def __init__(self, line, col, name, params, result, body, member, mutating):
        super().__init__(line, col)
        self.name = name
        self.params = params
        self.result = result
        self.body = body
        self.member = member
        self.mutating = mutating
        self.owner = None

    def label(self):
        kind = "Member" if self.member else "Func"
        return f"{kind} {self.name} -> {self.result}{' mutating' if self.mutating else ''}"

    def children(self):
        return self.params + [self.body]

    def accept(self, visitor):
        return visitor.visit_func(self)


class FieldDeclNode(Node):
    def __init__(self, line, col, name, type_name, mutable, type_at):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable
        self.type_at = type_at

    def label(self):
        return f"Field {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"


class StructNode(StmtNode):
    def __init__(self, line, col, name, fields, members):
        super().__init__(line, col)
        self.name = name
        self.fields = fields
        self.members = members

    def label(self):
        return f"Struct {self.name}"

    def children(self):
        return self.fields + self.members

    def accept(self, visitor):
        return visitor.visit_struct(self)


class InitNode(Node):
    def __init__(self, line, col, values):
        super().__init__(line, col)
        self.values = values

    def label(self):
        return "Init"

    def children(self):
        return self.values


class ObjectNode(StmtNode):
    def __init__(self, line, col, name, struct_name, mutable, init, type_at):
        super().__init__(line, col)
        self.name = name
        self.struct_name = struct_name
        self.mutable = mutable
        self.init = init
        self.type_at = type_at

    def label(self):
        return f"Object {self.name} {self.struct_name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return [self.init]

    def accept(self, visitor):
        return visitor.visit_object(self)


class ArrayType:
    def __init__(self, elem, size):
        self.elem = elem
        self.size = size

    def __eq__(self, other):
        return isinstance(other, ArrayType) and (self.elem, self.size) == (other.elem, other.size)

    def __hash__(self):
        return hash((self.elem, self.size))

    def __str__(self):
        return f"{self.elem}[{self.size}]"


class FieldSymbol:
    def __init__(self, index, name, type_name, mutable):
        self.index = index
        self.name = name
        self.type_name = type_name
        self.mutable = mutable


class StructSymbol:
    def __init__(self, node):
        self.name = node.name
        self.node = node
        self.fields = {}
        self.members = {}
        self.ir_type = None

    def __str__(self):
        return self.name


UNARY_OPS = {"not": NotNode, "minus": NegNode}


class Parser:
    def __init__(self, lines):
        self.lines = [toks for toks in ([t for t in l if t.kind != "endline"] for l in lines) if toks]
        self.line_pos = 0
        self.toks = []
        self.pos = 0
        self.depth = 0

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

    def at(self, kind):
        return self.peek() is not None and self.peek().kind == kind

    def parse_list(self, item, close, text):
        items = [item()]
        while self.at("comma"):
            self.eat()
            items.append(item())
        self.expect(close, f"',' or '{text}'")
        return items

    def parse_arg(self):
        tok = self.peek()
        expr = self.parse_expr()
        if tok is not None:
            expr.start = (tok.line, tok.col)
        return expr

    def parse_args(self):
        self.eat()
        if self.at("rparen"):
            self.eat()
            return []
        return self.parse_list(self.parse_arg, "rparen", ")")

    def chain_link(self, target, name):
        if target is None:
            return VarNode(name.line, name.col, name.text)
        return DotNode(name.line, name.col, target, name.text)

    def parse_chain(self):
        name, node = self.eat(), None
        while self.at("dot"):
            node = self.chain_link(node, name)
            self.eat()
            name = self.expect("ident", "a field name after '.'")
        if self.at("lparen"):
            return CallNode(name.line, name.col, name.text, self.parse_args(), node)
        node = self.chain_link(node, name)
        if not self.at("lbracket"):
            return node
        self.eat()
        tok = self.peek()
        index = self.parse_expr()
        self.expect("rbracket", "']'")
        return IndexNode(tok.line, tok.col, node, index)

    def parse_array_type(self, elem):
        if not self.at("lbracket"):
            return elem
        self.eat()
        size = self.expect("number", "an array size")
        if not 1 <= int(size.text) <= I32_MAX:
            raise CompileError(
                size.line, size.col, f"an array size must be 1 to {I32_MAX}, got {size.text}"
            )
        self.expect("rbracket", "']'")
        return ArrayType(elem, int(size.text))

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
            return self.parse_chain()
        if tok.kind == "kw_read":
            self.eat()
            return ReadNode(tok.line, tok.col)
        if tok.kind == "string":
            raise CompileError(
                tok.line, tok.col, "a string literal can only be printed with '\U0001f4e2'"
            )
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
        type_name = self.parse_array_type(TYPES[self.eat().kind])
        mutable = self.peek() is not None and self.peek().kind == "kw_mut"
        if mutable:
            self.eat()
        name = self.expect("ident", "a variable name")
        if self.peek() is None or self.peek().kind != "langle":
            raise CompileError(
                name.line, name.col, f"variable '{name.text}' needs an initialiser in ⟨⟩"
            )
        if isinstance(type_name, ArrayType):
            init = self.parse_init()
        else:
            self.eat()
            init = self.parse_expr()
            self.expect("rangle", "'⟩'")
        return DeclNode(name.line, name.col, name.text, type_name, mutable, init)

    def parse_ident_statement(self):
        first = self.peek()
        after = self.toks[self.pos + 1] if self.pos + 1 < len(self.toks) else None
        if after is not None and after.kind in ("ident", "kw_mut"):
            return self.parse_object()
        chain = self.parse_chain()
        if isinstance(chain, CallNode):
            return chain
        self.expect("assign", f"':=' after '{self.toks[self.pos - 1].text}'")
        value = self.parse_expr()
        if isinstance(chain, VarNode):
            return AssignNode(first.line, first.col, chain.name, value)
        if isinstance(chain, IndexNode):
            return IndexAssignNode(first.line, first.col, chain, value)
        return FieldAssignNode(first.line, first.col, chain, value)

    def parse_init_value(self):
        return self.parse_init() if self.at("langle") else self.parse_arg()

    def parse_init(self):
        tok = self.eat()
        return InitNode(tok.line, tok.col, self.parse_list(self.parse_init_value, "rangle", "⟩"))

    def parse_object(self):
        struct = self.eat()
        mutable = self.at("kw_mut")
        if mutable:
            self.eat()
        name = self.expect("ident", "an object name")
        if not self.at("langle"):
            raise CompileError(
                name.line, name.col, f"object '{name.text}' needs its field values in ⟨⟩"
            )
        return ObjectNode(
            name.line, name.col, name.text, struct.text, mutable, self.parse_init(),
            (struct.line, struct.col),
        )

    def parse_print_value(self):
        if self.at("string"):
            tok = self.eat()
            return StringNode(tok.line, tok.col, tok.text)
        return self.parse_arg()

    def parse_print(self):
        tok = self.eat()
        values = [self.parse_print_value()]
        while self.at("comma"):
            self.eat()
            values.append(self.parse_print_value())
        return PrintNode(tok.line, tok.col, values)

    def parse_scalar(self, what):
        if self.peek() is None or self.peek().kind not in TYPES:
            raise self.error(f"expected {what} (3⃣, 6⃣ or ❓)")
        return TYPES[self.eat().kind]

    def parse_param(self):
        type_name = self.parse_array_type(self.parse_scalar("a parameter type"))
        mutable = self.at("kw_mut")
        if mutable and not isinstance(type_name, ArrayType):
            tok = self.peek()
            raise CompileError(tok.line, tok.col, "only an array parameter can be \U0001f513")
        if mutable:
            self.eat()
        name = self.expect("ident", "a parameter name")
        return ParamNode(name.line, name.col, name.text, type_name, mutable)

    def parse_func(self, member=False):
        self.eat()
        mutating = self.at("kw_mut")
        if mutating and not member:
            tok = self.peek()
            raise CompileError(tok.line, tok.col, "only a member function can be \U0001f513")
        if mutating:
            self.eat()
        name = self.expect("ident", "a function name")
        self.expect("lparen", f"'(' after '{name.text}'")
        params = []
        if self.at("rparen"):
            self.eat()
        else:
            params = self.parse_list(self.parse_param, "rparen", ")")
        self.expect("arrow", "'→' and a result type")
        result = self.parse_scalar("a result type")
        self.end_line()
        body = self.parse_block(name.text, name.text)
        return FuncNode(name.line, name.col, name.text, params, result, body, member, mutating)

    def parse_field(self):
        tok = self.peek()
        if tok.kind not in TYPES and tok.kind != "ident":
            raise CompileError(tok.line, tok.col, "expected a field or a member function")
        self.eat()
        type_name = self.parse_array_type(TYPES[tok.kind]) if tok.kind in TYPES else tok.text
        mutable = self.at("kw_mut")
        if mutable:
            self.eat()
        name = self.expect("ident", "a field name")
        self.end_line()
        return FieldDeclNode(
            name.line, name.col, name.text, type_name, mutable, (tok.line, tok.col)
        )

    def parse_struct(self):
        self.eat()
        name = self.expect("ident", "a structure name")
        self.end_line()
        if self.peek_line() is not None:
            self.next_line()
        brace = self.expect("lbrace", f"'{{' on its own line after '{name.text}'")
        self.end_line()
        fields, members = [], []
        while (toks := self.peek_line()) is not None and toks[0].kind != "rbrace":
            self.next_line()
            if toks[0].kind == "kw_fn":
                members.append(self.parse_func(member=True))
            elif members:
                raise CompileError(
                    toks[0].line, toks[0].col, "fields come before the member functions"
                )
            else:
                fields.append(self.parse_field())
        if toks is None:
            raise CompileError(brace.line, brace.col, "'{' is never closed")
        if not fields:
            raise CompileError(name.line, name.col, f"structure '{name.text}' has no fields")
        self.next_line()
        self.eat()
        self.end_line()
        return StructNode(name.line, name.col, name.text, fields, members)

    def parse_block(self, after, function=None):
        if self.peek_line() is not None:
            self.next_line()
        brace = self.expect("lbrace", f"'{{' on its own line after '{after}'")
        self.end_line()
        self.depth += 1
        statements, exit_node = self.parse_body()
        self.depth -= 1
        toks = self.peek_line()
        if toks is None:
            raise CompileError(brace.line, brace.col, "'{' is never closed")
        if toks[0].kind != "rbrace":
            raise CompileError(
                toks[0].line, toks[0].col, "statement after '\U0001f6aa' in the same block"
            )
        if function is not None and exit_node is None:
            raise CompileError(
                toks[0].line, toks[0].col, f"function '{function}' must end with '\U0001f6aa'"
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
        return ExitNode(tok.line, tok.col, self.parse_arg())

    def parse_statement(self):
        tok = self.peek()
        if tok.kind in TYPES:
            return self.parse_decl()
        if tok.kind == "ident":
            return self.parse_ident_statement()
        if tok.kind in ("kw_fn", "kw_struct"):
            if self.depth:
                raise CompileError(tok.line, tok.col, f"'{tok.text}' is allowed only at top level")
            return self.parse_func() if tok.kind == "kw_fn" else self.parse_struct()
        if tok.kind == "kw_print":
            return self.parse_print()
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
        self.functions = {}
        self.structs = {}
        self.function = None
        self.struct = None

    def lookup(self, node):
        frames = self.scopes[::-1]
        if self.struct is not None:
            frames = frames[:-1] + [self.struct.fields, self.scopes[0]]
        for frame in frames:
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

    def assignable(self, expr, want):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return True
        if (value := literal_value(expr)) is not None and have == "i64" and want == "i32":
            raise CompileError(expr.line, expr.col, f"constant {value} does not fit in i32")
        return False

    def check_assignable(self, expr, want, at, what):
        if not self.assignable(expr, want):
            raise CompileError(
                at.line, at.col, f"cannot {what} of type {want} with a value of type {expr.type}"
            )

    def check_redeclared(self, node):
        if node.name in self.scopes[-1]:
            raise CompileError(
                node.line, node.col, f"variable '{node.name}' is already declared in this block"
            )

    def resolve_type(self, name, at):
        if isinstance(name, ArrayType) or name in IR_TYPES:
            return name
        if name not in self.structs:
            raise CompileError(*at, f"unknown structure '{name}'")
        return self.structs[name]

    def check_args(self, node, func):
        want, have = len(func.params), len(node.args)
        if want != have:
            at = start(node.args[want]) if have > want else (node.line, node.col)
            noun = "argument" if want == 1 else "arguments"
            raise CompileError(*at, f"'{func.name}' takes {want} {noun}, got {have}")
        for arg, param in zip(node.args, func.params):
            arg.accept(self)
            if isinstance(param.type_name, ArrayType):
                self.check_array_arg(arg, param, func)
            elif not self.assignable(arg, param.type_name):
                raise CompileError(
                    *start(arg),
                    f"argument '{param.name}' of '{func.name}' must be {param.type_name}, "
                    f"got {arg.type}",
                )

    def check_body(self, node, struct=None):
        frame = {}
        for param in node.params:
            if param.name in frame:
                raise CompileError(
                    param.line, param.col, f"parameter '{param.name}' is declared twice"
                )
            frame[param.name] = param
        saved = self.scopes, self.function, self.struct
        self.scopes, self.function, self.struct = [self.scopes[0], frame], node, struct
        for statement in node.body.statements:
            statement.accept(self)
        node.body.exit.accept(self)
        self.scopes, self.function, self.struct = saved

    def check_init(self, init, struct):
        fields = list(struct.fields.values())
        if len(init.values) != len(fields):
            extra = init.values[len(fields)] if len(init.values) > len(fields) else init
            raise CompileError(
                *start(extra),
                f"'{struct}' has {len(fields)} fields, got {len(init.values)} values",
            )
        for value, field in zip(init.values, fields):
            if isinstance(field.type_name, ArrayType):
                if not isinstance(value, InitNode):
                    raise CompileError(
                        *start(value),
                        f"field '{field.name}' of '{struct}' needs its values in ⟨⟩",
                    )
                self.check_array_init(value, field.type_name, field.name)
            elif isinstance(field.type_name, StructSymbol):
                if not isinstance(value, InitNode):
                    raise CompileError(
                        *start(value),
                        f"field '{field.name}' of '{struct}' needs its values in ⟨⟩",
                    )
                self.check_init(value, field.type_name)
            elif isinstance(value, InitNode):
                raise CompileError(
                    value.line, value.col, f"field '{field.name}' of '{struct}' is not a structure"
                )
            else:
                value.accept(self)
                self.check_assignable(value, field.type_name, value, f"initialise '{field.name}'")
        init.struct = struct

    def check_array_init(self, init, array, name):
        count = len(init.values)
        if count not in (1, array.size):
            extra = init.values[array.size] if count > array.size else init
            raise CompileError(
                *start(extra), f"'{name}' has {array.size} elements, got {count} values"
            )
        for value in init.values:
            if isinstance(value, InitNode):
                raise CompileError(
                    value.line, value.col, f"an element of '{name}' is {array.elem}, not a structure"
                )
            value.accept(self)
            self.check_assignable(value, array.elem, value, f"initialise an element of '{name}'")
        init.array = array

    def check_array_arg(self, arg, param, func):
        if arg.type != param.type_name:
            raise CompileError(
                *start(arg),
                f"argument '{param.name}' of '{func.name}' must be {param.type_name}, "
                f"got {arg.type}",
            )
        if param.mutable:
            self.check_chain(arg, f"pass to \U0001f513 '{param.name}'")

    def visit_index(self, node):
        array = node.target.accept(self)
        if not isinstance(array, ArrayType):
            target = node.target
            raise CompileError(
                target.line, target.col, f"cannot index '{node.name}' of type {array}"
            )
        if (have := node.index.accept(self)) not in ("i32", "i64"):
            raise CompileError(node.line, node.col, f"an index must be i32 or i64, got {have}")
        value = literal_value(node.index)
        if value is not None and not 0 <= value < array.size:
            raise CompileError(
                node.line, node.col,
                f"index {value} is out of range for '{node.name}' of size {array.size}",
            )
        node.type = array.elem
        return node.type

    def visit_index_assign(self, node):
        target = node.target
        target.accept(self)
        self.check_chain(target.target, "assign")
        node.value.accept(self)
        self.check_assignable(
            node.value, target.type, node, f"assign to an element of '{target.name}'"
        )

    def require_mutating(self, at, action):
        if not self.function.mutating:
            raise CompileError(
                at.line, at.col, f"'{self.function.name}' is not \U0001f513 and cannot {action}"
            )

    def check_chain(self, chain, action):
        if isinstance(chain, DotNode):
            self.check_chain(chain.target, action)
            symbol = chain.field
        else:
            symbol = chain.decl
            if isinstance(symbol, FieldSymbol):
                self.require_mutating(chain, f"{action} through '{chain.name}'")
        if not symbol.mutable:
            raise CompileError(
                chain.line, chain.col,
                f"cannot {action} through '{chain.name}': it is not \U0001f513",
            )

    def visit_dot(self, node):
        struct = node.target.accept(self)
        if not isinstance(struct, StructSymbol):
            raise CompileError(
                node.line, node.col, f"cannot select '{node.name}' from a value of type {struct}"
            )
        if node.name not in struct.fields:
            raise CompileError(
                node.line, node.col, f"structure '{struct}' has no field '{node.name}'"
            )
        node.field = struct.fields[node.name]
        node.type = node.field.type_name
        return node.type

    def visit_field_assign(self, node):
        target = node.target
        if not is_scalar(have := target.accept(self)):
            raise CompileError(
                target.line, target.col,
                f"cannot assign to '{target.name}': {aggregate(have)} are not copied",
            )
        self.check_chain(target, "assign")
        node.value.accept(self)
        self.check_assignable(node.value, target.type, node, f"assign to '{target.name}'")

    def visit_program(self, node):
        for statement in node.statements:
            if isinstance(statement, FuncNode):
                if statement.name in self.functions:
                    raise CompileError(
                        statement.line, statement.col,
                        f"function '{statement.name}' is already declared",
                    )
                self.functions[statement.name] = statement
        for statement in node.statements:
            statement.accept(self)
        node.exit.accept(self)

    def visit_func(self, node):
        self.check_body(node)

    def visit_struct(self, node):
        if node.name in IR_TYPES:
            raise CompileError(node.line, node.col, f"'{node.name}' cannot name a structure")
        if node.name in self.structs:
            raise CompileError(
                node.line, node.col, f"structure '{node.name}' is already declared"
            )
        struct = StructSymbol(node)
        for index, field in enumerate(node.fields):
            if field.name in struct.fields:
                raise CompileError(
                    field.line, field.col,
                    f"field '{field.name}' is declared twice in '{node.name}'",
                )
            type_name = self.resolve_type(field.type_name, field.type_at)
            struct.fields[field.name] = FieldSymbol(index, field.name, type_name, field.mutable)
        for member in node.members:
            if member.name in struct.members:
                raise CompileError(
                    member.line, member.col,
                    f"member function '{member.name}' is declared twice in '{node.name}'",
                )
            member.owner = struct
            struct.members[member.name] = member
        node.symbol = struct
        self.structs[node.name] = struct
        for member in node.members:
            self.check_body(member, struct)

    def visit_object(self, node):
        self.check_redeclared(node)
        struct = self.resolve_type(node.struct_name, node.type_at)
        if is_scalar(struct):
            raise CompileError(*node.type_at, f"'{node.struct_name}' is not a structure")
        self.check_init(node.init, struct)
        node.type_name = struct
        self.scopes[-1][node.name] = node

    def visit_call(self, node):
        if node.target is not None:
            struct = node.target.accept(self)
            if not isinstance(struct, StructSymbol):
                raise CompileError(
                    node.line, node.col, f"cannot call '{node.name}' on a value of type {struct}"
                )
            if node.name not in struct.members:
                raise CompileError(
                    node.line, node.col,
                    f"structure '{struct}' has no member function '{node.name}'",
                )
            func = struct.members[node.name]
            if func.mutating:
                self.check_chain(node.target, f"call mutating '{func.name}'")
        elif self.struct is not None and node.name in self.struct.members:
            func = self.struct.members[node.name]
            if func.mutating:
                self.require_mutating(node, f"call mutating '{func.name}'")
        elif node.name in self.functions:
            func = self.functions[node.name]
        else:
            raise CompileError(node.line, node.col, f"function '{node.name}' is not declared")
        self.check_args(node, func)
        node.func = func
        node.type = func.result
        return node.type

    def visit_print(self, node):
        for value in node.values:
            if not isinstance(value, StringNode):
                value.accept(self)

    def visit_read(self, node):
        node.type = "i32"
        return node.type

    def visit_decl(self, node):
        self.check_redeclared(node)
        if isinstance(node.type_name, ArrayType):
            self.check_array_init(node.init, node.type_name, node.name)
        else:
            node.init.accept(self)
            self.check_assignable(node.init, node.type_name, node, f"initialise '{node.name}'")
        self.scopes[-1][node.name] = node

    def visit_assign(self, node):
        decl = self.lookup(node)
        if not is_scalar(decl.type_name):
            raise CompileError(
                node.line, node.col,
                f"cannot assign to '{node.name}': {aggregate(decl.type_name)} are not copied",
            )
        if isinstance(decl, FieldSymbol):
            self.require_mutating(node, f"assign to field '{node.name}'")
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
        value = node.value
        value.accept(self)
        if self.function is not None and not self.assignable(value, self.function.result):
            raise CompileError(
                *start(value),
                f"'{self.function.name}' must leave with {self.function.result}, got {value.type}",
            )
        if self.function is None and not is_scalar(value.type):
            raise CompileError(
                *start(value), f"'\U0001f6aa' needs i32, i64 or bool, got {value.type}"
            )

    def visit_binop(self, node):
        lt, rt = node.left.accept(self), node.right.accept(self)
        for have in (lt, rt):
            if not is_scalar(have):
                noun = "an array" if isinstance(have, ArrayType) else "an object"
                raise CompileError(node.line, node.col, f"cannot apply '{node.op}' to {noun}")
        if node.op in ARITH or node.op in ORDER_OPS:
            if "bool" in (lt, rt):
                raise CompileError(node.line, node.col, f"cannot apply '{node.op}' to bool")
            node.type = "bool" if node.op in ORDER_OPS else wider(lt, rt)
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
        if (have := node.operand.accept(self)) == "bool" or not is_scalar(have):
            raise CompileError(node.line, node.col, f"cannot apply '-' to {have}")
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


class Optimizer:
    def run(self, program):
        while any([
            self.drop_unused_functions(program),
            self.drop_unused_variables(program),
            self.inline(program),
        ]):
            pass

    def drop_unused_functions(self, program):
        roots = [s for s in program.statements if not isinstance(s, (FuncNode, StructNode))]
        pending = [n.func for root in roots + [program.exit] for n in walk(root) if isinstance(n, CallNode)]
        pending += [n.func for n in walk(program) if isinstance(n, CallNode) and n.func.owner]
        reached = set()
        while pending:
            func = pending.pop()
            if func not in reached:
                reached.add(func)
                pending += [n.func for n in walk(func.body) if isinstance(n, CallNode)]
        before = sum(isinstance(n, FuncNode) for n in walk(program))
        program.statements = [
            s for s in program.statements if not isinstance(s, FuncNode) or s in reached
        ]
        for struct in program.statements:
            if isinstance(struct, StructNode):
                struct.members = [m for m in struct.members if m in reached]
                struct.symbol.members = {m.name: m for m in struct.members}
        return sum(isinstance(n, FuncNode) for n in walk(program)) != before

    def drop_unused_variables(self, program):
        read = set(reads(program))
        assigns = [n for n in walk(program) if isinstance(n, AssignNode)]
        dead = {
            n for n in walk(program)
            if isinstance(n, DeclNode) and is_scalar(n.type_name) and n not in read
            and not has_effect(n.init)
            and not any(has_effect(a.value) for a in assigns if a.decl is n)
        }
        for node in [n for n in walk(program) if isinstance(n, (ProgramNode, BlockNode))]:
            node.statements = [
                s for s in node.statements
                if s not in dead and not (isinstance(s, AssignNode) and s.decl in dead)
            ]
        return bool(dead)

    def inlinable(self, func, counts):
        return (
            func.owner is None and counts[func] == 1
            and not any(isinstance(p.type_name, ArrayType) for p in func.params)
            and not any(isinstance(n, ExitNode) for s in func.body.statements for n in walk(s))
            and func not in callees(func)
        )

    def call_to_inline(self, statement, counts):
        if isinstance(statement, WhileNode):
            return None
        own = own_nodes(statement)
        effects = [n for n in own if isinstance(n, (CallNode, ReadNode))]
        if len(effects) != 1 or not isinstance(call := effects[0], CallNode):
            return None
        inside = set(walk(call))
        if any(isinstance(n, VarNode) and n not in inside for n in own):
            return None
        return call if self.inlinable(call.func, counts) else None

    def splice(self, program, container, statement, call):
        func = call.func
        body_nodes = set(walk(func.body))
        memo = {id(n): n for n in walk(program) if n not in body_nodes}
        for struct in program.statements:
            if isinstance(struct, StructNode):
                memo[id(struct.symbol)] = struct.symbol
                memo.update({id(f): f for f in struct.symbol.fields.values()})
        hoisted = []
        for param, arg in zip(func.params, call.args):
            decl = DeclNode(
                arg.line, arg.col, f"{func.name}.{param.name}", param.type_name, False, arg
            )
            memo[id(param)] = decl
            hoisted.append(decl)
        body = copy.deepcopy(func.body, memo)
        locals_ = [n for n in walk(body) if isinstance(n, (DeclNode, ObjectNode))]
        for decl in locals_:
            decl.name = f"{func.name}.{decl.name}"
        renamed = set(hoisted + locals_)
        for node in walk(body):
            if isinstance(node, (VarNode, AssignNode)) and node.decl in renamed:
                node.name = node.decl.name

        result = DeclNode(
            call.line, call.col, f"{func.name}.0", func.result, False, body.exit.value
        )
        hoisted += body.statements + [result]
        at = len(container.statements)
        if statement is not container.exit:
            at = container.statements.index(statement)
        if statement is call:
            container.statements[at:at + 1] = hoisted
            return
        var = VarNode(call.line, call.col, result.name)
        var.decl, var.type = result, func.result
        replace(statement, call, var)
        container.statements[at:at] = hoisted

    def inline(self, program):
        counts = Counter(n.func for n in walk(program) if isinstance(n, CallNode))
        for container in [n for n in walk(program) if isinstance(n, (ProgramNode, BlockNode))]:
            for statement in container.statements + [container.exit]:
                if statement is not None and (call := self.call_to_inline(statement, counts)):
                    self.splice(program, container, statement, call)
                    return True
        return False


class CodeGen:
    def __init__(self):
        self.module = ir.Module(name="kaguya")
        self.module.triple = llvm.get_default_triple()

        c_type = ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True)
        self.printf = ir.Function(self.module, c_type, name="printf")
        self.scanf = ir.Function(self.module, c_type, name="scanf")
        fd_type = ir.FunctionType(I32, [I32, ir.PointerType(I8)], var_arg=True)
        self.dprintf = ir.Function(self.module, fd_type, name="dprintf")
        self.exit = ir.Function(self.module, ir.FunctionType(ir.VoidType(), [I32]), name="exit")

        self.builder = self.function = self.func_node = self.this = None
        self.functions = {}
        self.slots = {}
        self.strings = {}

    def alloca_entry(self, type, name):
        with self.builder.goto_entry_block():
            return self.builder.alloca(type, name=name)

    def global_slot(self, type, name):
        slot = ir.GlobalVariable(self.module, type, name=f"kg${name}")
        slot.initializer = ir.Constant(type, None)
        return slot

    def ir_type(self, type_name):
        if isinstance(type_name, ArrayType):
            return ir.ArrayType(IR_TYPES[type_name.elem], type_name.size)
        if isinstance(type_name, StructSymbol):
            return type_name.ir_type
        return IR_TYPES[type_name]

    def param_type(self, param):
        if isinstance(param.type_name, ArrayType):
            return self.array_param_type(param)
        return IR_TYPES[param.type_name]

    def slot_of(self, decl):
        if isinstance(decl, FieldSymbol):
            return self.field_slot(self.this, decl)
        return self.slots[decl]

    def declare_function(self, node):
        fn_type = ir.FunctionType(
            IR_TYPES[node.result], [self.param_type(param) for param in node.params]
        )
        self.functions[node] = ir.Function(self.module, fn_type, name=f"kg.{node.name}")

    def emit_function(self, node, fn, args):
        saved = self.builder, self.function, self.func_node
        self.builder = ir.IRBuilder(fn.append_basic_block("entry"))
        self.function, self.func_node = fn, node
        for param, arg in zip(node.params, args):
            if isinstance(param.type_name, ArrayType):
                self.bind_array_param(param, arg)
                continue
            slot = self.builder.alloca(IR_TYPES[param.type_name], name=param.name)
            self.builder.store(arg, slot)
            self.slots[param] = slot
        node.body.accept(self)
        self.builder, self.function, self.func_node = saved

    def array_param_type(self, param):
        return self.ir_type(param.type_name).as_pointer()

    def bind_array_param(self, param, arg):
        self.slots[param] = arg

    def array_arg(self, arg):
        return self.address(arg)

    def element_slot(self, slot, index):
        return self.builder.gep(slot, [ir.Constant(I32, 0), index])

    def count_loop(self, first, size, emit):
        counter = self.alloca_entry(I32, "i")
        self.builder.store(ir.Constant(I32, first), counter)
        cond_bb = self.function.append_basic_block("loop.cond")
        body_bb = self.function.append_basic_block("loop.body")
        end_bb = self.function.append_basic_block("loop.end")
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        i = self.builder.load(counter)
        self.builder.cbranch(self.builder.icmp_signed("<", i, ir.Constant(I32, size)), body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        emit(i)
        self.builder.store(self.builder.add(i, ir.Constant(I32, 1)), counter)
        self.builder.branch(cond_bb)
        self.builder.position_at_end(end_bb)

    def store_array_init(self, init, slot):
        array = init.array
        values = [self.coerce(v.accept(self), v.type, array.elem) for v in init.values]
        if len(values) == array.size:
            for i, value in enumerate(values):
                self.builder.store(value, self.element_slot(slot, ir.Constant(I32, i)))
        else:
            self.count_loop(0, array.size, lambda i: self.builder.store(values[0], self.element_slot(slot, i)))

    def print_array(self, slot, array):
        self.print_text("[")
        self.print_scalar(self.builder.load(self.element_slot(slot, ir.Constant(I32, 0))), array.elem)

        def rest(i):
            self.print_text(", ")
            self.print_scalar(self.builder.load(self.element_slot(slot, i)), array.elem)

        self.count_loop(1, array.size, rest)
        self.print_text("]")

    def print_slot(self, slot, type_name):
        if is_scalar(type_name):
            self.print_scalar(self.builder.load(slot), type_name)
        elif isinstance(type_name, ArrayType):
            self.print_array(slot, type_name)
        else:
            self.print_object(slot, type_name)

    def field_slot(self, slot, field):
        return self.builder.gep(slot, [ir.Constant(I32, 0), ir.Constant(I32, field.index)])

    def address(self, chain):
        if isinstance(chain, DotNode):
            return self.field_slot(self.address(chain.target), chain.field)
        return self.slot_of(chain.decl)

    def declare_struct(self, node):
        struct = node.symbol
        struct.ir_type = self.module.context.get_identified_type(node.name)
        struct.ir_type.set_body(*[self.ir_type(f.type_name) for f in struct.fields.values()])
        for member in node.members:
            fn_type = ir.FunctionType(
                IR_TYPES[member.result],
                [struct.ir_type.as_pointer()] + [self.param_type(p) for p in member.params],
            )
            self.functions[member] = ir.Function(
                self.module, fn_type, name=f"kg.{node.name}.{member.name}"
            )

    def visit_struct(self, node):
        for member in node.members:
            fn = self.functions[member]
            self.this = fn.args[0]
            self.emit_function(member, fn, fn.args[1:])
        self.this = None

    def store_init(self, init, slot):
        for value, field in zip(init.values, init.struct.fields.values()):
            target = self.field_slot(slot, field)
            if isinstance(field.type_name, ArrayType):
                self.store_array_init(value, target)
            elif isinstance(value, InitNode):
                self.store_init(value, target)
            else:
                self.builder.store(
                    self.coerce(value.accept(self), value.type, field.type_name), target
                )

    def visit_object(self, node):
        if node not in self.slots:
            self.slots[node] = self.alloca_entry(node.type_name.ir_type, node.name)
        self.store_init(node.init, self.slots[node])

    def visit_dot(self, node):
        return self.builder.load(self.address(node))

    def visit_field_assign(self, node):
        value = self.coerce(node.value.accept(self), node.value.type, node.target.type)
        self.builder.store(value, self.address(node.target))

    def checked_element(self, node):
        slot = self.address(node.target)
        index = self.coerce(node.index.accept(self), node.index.type, "i64")
        size = node.target.type.size
        ok_bb = self.function.append_basic_block("index.ok")
        oob_bb = self.function.append_basic_block("index.oob")
        self.builder.cbranch(
            self.builder.icmp_unsigned("<", index, ir.Constant(I64, size)), ok_bb, oob_bb
        )

        self.builder.position_at_end(oob_bb)
        data = (
            f"runtime error: line %d:%d: index %lld out of range for '{node.name}' "
            f"of size %d\n"
        ).encode() + b"\0"
        name = self.strings.setdefault(data, f"str.{len(self.strings)}")
        self.builder.call(self.dprintf, [
            ir.Constant(I32, 2), self.global_string(name, data), ir.Constant(I32, node.line),
            ir.Constant(I32, node.col), index, ir.Constant(I32, size),
        ])
        self.builder.call(self.exit, [ir.Constant(I32, 1)])
        self.builder.unreachable()
        self.builder.position_at_start(ok_bb)
        return self.element_slot(slot, index)

    def visit_index(self, node):
        return self.builder.load(self.checked_element(node))

    def visit_index_assign(self, node):
        value = self.coerce(node.value.accept(self), node.value.type, node.target.type)
        self.builder.store(value, self.checked_element(node.target))

    def print_object(self, slot, struct):
        self.print_text(f"{struct} {{ ")
        for i, field in enumerate(struct.fields.values()):
            self.print_text(f"{', ' if i else ''}{field.name}: ")
            self.print_slot(self.field_slot(slot, field), field.type_name)
        self.print_text(" }")

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

    def print_text(self, text):
        data = text.encode() + b"\0"
        name = self.strings.setdefault(data, f"str.{len(self.strings)}")
        self.builder.call(
            self.printf,
            [self.global_string("fmt_s", FORMAT_S), self.global_string(name, data)],
        )

    def print_scalar(self, value, type_name):
        if type_name == "bool":
            text = self.builder.select(
                value,
                self.global_string("str.true", b"true\0"),
                self.global_string("str.false", b"false\0"),
            )
            self.builder.call(self.printf, [self.global_string("fmt_s", FORMAT_S), text])
        elif type_name == "i32":
            self.builder.call(self.printf, [self.global_string("fmt_d", FORMAT_D), value])
        else:
            self.builder.call(self.printf, [self.global_string("fmt_lld", FORMAT_LLD), value])

    def print_bool(self, value):
        fmt = self.builder.select(
            value,
            self.global_string("fmt_true", FORMAT_TRUE),
            self.global_string("fmt_false", FORMAT_FALSE),
        )
        self.builder.call(self.printf, [fmt])

    def visit_program(self, node):
        for statement in node.statements:
            if isinstance(statement, FuncNode):
                self.declare_function(statement)
            elif isinstance(statement, StructNode):
                self.declare_struct(statement)
        for statement in node.statements:
            if isinstance(statement, (DeclNode, ObjectNode)):
                type = self.ir_type(statement.type_name)
                self.slots[statement] = self.global_slot(type, statement.name)
        self.function = ir.Function(self.module, ir.FunctionType(I32, []), name="main")
        self.builder = ir.IRBuilder(self.function.append_basic_block("entry"))
        for statement in node.statements:
            statement.accept(self)
        node.exit.accept(self)
        return self.module

    def visit_func(self, node):
        fn = self.functions[node]
        self.emit_function(node, fn, fn.args)

    def visit_decl(self, node):
        if node not in self.slots:
            self.slots[node] = self.alloca_entry(self.ir_type(node.type_name), node.name)
        if isinstance(node.type_name, ArrayType):
            self.store_array_init(node.init, self.slots[node])
            return
        value = self.coerce(node.init.accept(self), node.init.type, node.type_name)
        self.builder.store(value, self.slots[node])

    def visit_assign(self, node):
        value = self.coerce(node.value.accept(self), node.value.type, node.decl.type_name)
        self.builder.store(value, self.slot_of(node.decl))

    def visit_call(self, node):
        args = [
            self.array_arg(arg) if isinstance(param.type_name, ArrayType)
            else self.coerce(arg.accept(self), arg.type, param.type_name)
            for arg, param in zip(node.args, node.func.params)
        ]
        if node.func.owner is not None:
            args = [self.this if node.target is None else self.address(node.target)] + args
        return self.builder.call(self.functions[node.func], args)

    def visit_read(self, node):
        slot = self.alloca_entry(I32, "read")
        self.builder.call(self.scanf, [self.global_string("fmt_d", FORMAT_D), slot])
        return self.builder.load(slot)

    def visit_print(self, node):
        for value in node.values:
            if isinstance(value, StringNode):
                self.print_text(value.text[1:-1])
            elif is_scalar(value.type):
                self.print_scalar(value.accept(self), value.type)
            else:
                self.print_slot(self.address(value), value.type)
        self.print_text("\n")

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
        if self.func_node is not None:
            self.builder.ret(self.coerce(value, node.value.type, self.func_node.result))
            return
        if node.value.type == "bool":
            self.print_bool(value)
        else:
            self.print_int(self.coerce(value, node.value.type, "i64"))
        self.builder.ret(ir.Constant(I32, 0))

    def visit_binop(self, node):
        want = wider(node.left.type, node.right.type)
        left = self.coerce(node.left.accept(self), node.left.type, want)
        right = self.coerce(node.right.accept(self), node.right.type, want)
        if node.op in ARITH:
            return getattr(self.builder, ARITH[node.op])(left, right)
        return self.builder.icmp_signed(node.op, left, right)

    def visit_not(self, node):
        return self.builder.xor(node.operand.accept(self), ir.Constant(I1, True))

    def visit_neg(self, node):
        if hasattr(node, "folded"):
            return ir.Constant(IR_TYPES[node.type], node.folded)
        return self.builder.neg(self.coerce(node.operand.accept(self), node.operand.type, node.type))

    def visit_var(self, node):
        return self.builder.load(self.slot_of(node.decl))

    def visit_const(self, node):
        return ir.Constant(IR_TYPES[node.type], node.value)

    def visit_bool(self, node):
        return ir.Constant(I1, node.value)


def main(argv):
    flags = {a for a in argv[1:] if a.startswith("--")}
    args = [a for a in argv[1:] if not a.startswith("--")]
    ast_only, optimize = "--ast" in flags, "--optimize" in flags
    if flags - {"--ast", "--optimize"} or len(args) != (1 if ast_only else 2):
        print(
            f"usage: {argv[0]} [--optimize] <input> <output.ll>\n"
            f"       {argv[0]} [--optimize] --ast <input>",
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
        if ast_only and not optimize:
            print("\n".join(program.dump()))
            return 0
        program.accept(SemanticChecker())
        if optimize:
            Optimizer().run(program)
        if ast_only:
            print("\n".join(program.dump()))
            return 0
        module = program.accept(CodeGen())
    except CompileError as exc:
        print(f"compilation error: line {exc.line}:{exc.col}: {exc.message}", file=sys.stderr)
        return 1

    with open(args[1], "w") as f:
        f.write(str(module))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
