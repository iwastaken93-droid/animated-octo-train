"""Regression test for usage-evidence naming (struct_sweep_name.NameMixin).

1. END-TO-END: hand-encoded protos (raw words + consts, no compiler needed)
   through the real Structurer entry point in both modes: off-mode output
   stays R-numbered, names-mode renames exactly the evidenced register.
2. TREE: pipeline-exact Node trees through _apply_names covering the
   evidence families plus every refusal gate (multi-def, compound, hbind,
   string/comment safety, collisions, register receivers).
3. SPELLING: the spelling-owner unit cases.

Run: python naming_check.py. Exit 1 with FAIL lines on any mismatch.
"""
import sys

sys.path.insert(0, ".")
import parser_v14
from parser_v14 import Proto, Const, Bytecode
from opcodes_v14 import NAME_TO_OP
from struct_node import Node, Cond, render_all
from struct_v14 import Structurer

fails = []


def check(title, cond):
    print(("PASS " if cond else "FAIL ") + title)
    if not cond:
        fails.append(title)


# -- word encoders (opcodes_v14 layout: ABC header, AD header, AUX word) --
def _abc(op, A, B=0, C=0):
    return NAME_TO_OP[op] | (A << 8) | (B << 16) | (C << 24)


def _ad(op, A, D):
    return NAME_TO_OP[op] | (A << 8) | ((D & 0xFFFF) << 16)


def _str(s):
    return Const(parser_v14.LBC_CONSTANT_STRING, ("strid", 99, s.encode()))


def _import(stridx):
    return Const(parser_v14.LBC_CONSTANT_IMPORT,
                 ("import", (1 << 30) | (stridx << 20), (1, stridx, -1, -1)))


def _tail(ureg, kprint):
    """print(ureg); bare return. Print machinery lives in R10/R11."""
    return [_ad("GETIMPORT", 10, kprint), 0, _abc("MOVE", 11, ureg),
            _abc("CALL", 10, 2, 1), _abc("RETURN", 0, 1)]


def _run(words, consts, names):
    p = Proto(id=0, maxstack=12, numparams=0, code=list(words),
              consts=list(consts), debugname=b"t")
    bc = Bytecode(version=14, protos=[p], mainid=0)
    r = Structurer(bc, p, names=names).structure()
    return r.stats.get("renamed", 0), render_all(r.nodes)


def _endtoend(title, words, consts, want_off, want_on):
    n0, off = _run(words, consts, False)
    n1, on = _run(words, consts, True)
    ok = (n0, off) == (0, want_off) and on == want_on and n1 == 1
    check("e2e-" + title, ok)
    if not ok:
        print("  off: ", off)
        print("  on:  ", on, "renamed=", n1)


def _consts(*names):
    out = [_str(s) for s in names]
    out.append(_str("print"))
    out.append(_import(len(out) - 1))
    return out


# R5 = R0:GetAttribute('Speed')
_endtoend("getattr", [
    _ad("LOADK", 7, 1),
    _abc("NAMECALL", 5, 0, 0), 0,
    _abc("CALL", 5, 3, 2),
] + _tail(5, 3), _consts("GetAttribute", "Speed"),
    ["R5 = R0:GetAttribute('Speed')  -- GetAttribute method", "print(R5)"],
    ["speed = R0:GetAttribute('Speed')  -- GetAttribute method", "print(speed)"])

# R5 = R0:Connect(R7)
_endtoend("connect", [
    _abc("NAMECALL", 5, 0, 0), 0,
    _abc("CALL", 5, 3, 2),
] + _tail(5, 2), _consts("Connect"),
    ["R5 = R0:Connect(R7)  -- Connect method", "print(R5)"],
    ["connection = R0:Connect(R7)  -- Connect method", "print(connection)"])

# R5 = R0.Touched:Connect(R7)
_endtoend("event", [
    _abc("GETTABLEKS", 4, 0, 0), 0,
    _abc("NAMECALL", 5, 4, 0), 1,
    _abc("CALL", 5, 3, 2),
] + _tail(5, 3), _consts("Touched", "Connect"),
    ["R5 = R0.Touched:Connect(R7)  -- Connect method", "print(R5)"],
    ["touchedConnection = R0.Touched:Connect(R7)  -- Connect method",
     "print(touchedConnection)"])

# R4 = R0:Clone() (self-only call: B=2)
_endtoend("clone", [
    _abc("NAMECALL", 4, 0, 0), 0,
    _abc("CALL", 4, 2, 2),
    _ad("GETIMPORT", 10, 2), 0, _abc("MOVE", 11, 4),
    _abc("CALL", 10, 2, 1), _abc("RETURN", 0, 1),
], _consts("Clone"),
    ["R4 = R0:Clone()  -- Clone method", "print(R4)"],
    ["clone = R0:Clone()  -- Clone method", "print(clone)"])

# R6 = require(R0.Foo)
_endtoend("require", [
    _ad("GETIMPORT", 6, 3), (1 << 30),
    _abc("GETTABLEKS", 7, 0, 0), 4,
    _abc("CALL", 6, 2, 2),
    _ad("GETIMPORT", 10, 2), (1 << 30) | (1 << 20), _abc("MOVE", 11, 6),
    _abc("CALL", 10, 2, 1), _abc("RETURN", 0, 1),
], [_str("require"), _str("print"), _import(1), _import(0), _str("Foo")],
    ["R6 = require(R0.Foo)", "print(R6)"],
    ["Foo = require(R0.Foo)", "print(Foo)"])

# R8 = Instance.new('Part')
_endtoend("new", [
    _abc("GETGLOBAL", 9, 0, 0), 0,
    _abc("GETTABLEKS", 10, 9, 0), 1,
    _ad("LOADK", 11, 2),
    _abc("CALL", 10, 2, 2),
    _abc("MOVE", 8, 10),
] + _tail(8, 4), _consts("Instance", "new", "Part"),
    ["R10 = Instance.new('Part')", "print(R10)"],
    ["part = Instance.new('Part')", "print(part)"])


# -- tree cases: _apply_names is pure tree logic; bypass __init__ --
def _stmt(t, **kw):
    return Node("stmt", t, **kw)


_S = Structurer.__new__(Structurer)


def _tree(title, nodes, want_n, want_lines, numparams=0, upvals=()):
    n = _S._apply_names(nodes, numparams, tuple(upvals))
    got = render_all(nodes)
    ok = (n == want_n and got == want_lines)
    check("tree-" + title, ok)
    if not ok:
        print("  want:", want_lines)
        print("  got: ", got)


_tree("signal-arg", [
    _stmt("R9 = R0:GetPropertyChangedSignal(\"Health\"):Connect(R1)"),
    _stmt("print(R9)"),
], 1, ["healthChangedConnection = R0:GetPropertyChangedSignal(\"Health\"):Connect(R1)",
       "print(healthChangedConnection)"])

_tree("clone-path", [
    _stmt("R4 = Game.Folder:Clone()"),
    _stmt("print(R4)"),
], 1, ["folderClone = Game.Folder:Clone()", "print(folderClone)"])

_tree("require-wfc", [
    _stmt("R7 = require(R0:WaitForChild(\"GameNotifications\"))"),
    _stmt("print(R7)"),
], 1, ["GameNotifications = require(R0:WaitForChild(\"GameNotifications\"))",
       "print(GameNotifications)"])

_tree("multidef", [
    _stmt("R6 = R0.Touched:Connect(R1)"),
    _stmt("R6 = R0:Clone()"),
    _stmt("print(R6)"),
], 2, ["touchedConnection = R0.Touched:Connect(R1)", "clone = R0:Clone()",
       "print(clone)"])

_tree("compound", [
    _stmt("R5 = R0:Connect(R1)"),
    _stmt("R5 += 1"),
], 0, ["R5 = R0:Connect(R1)", "R5 += 1"])

_tree("strings", [
    _stmt("R5 = R0:GetAttribute(\"Speed\")"),
    _stmt("R6 = \"R5 lives here\""),
    _stmt("print(R5, R6)"),
], 1, ["speed = R0:GetAttribute(\"Speed\")", "R6 = \"R5 lives here\"",
       "print(speed, R6)"])

_tree("comments", [
    _stmt("R11 = R8:add(7)  -- add method"),
    _stmt("-- capture VAL R11"),
    _stmt("print(R11)"),
], 0, ["R11 = R8:add(7)  -- add method", "-- capture VAL R11", "print(R11)"])

_tree("collision", [
    _stmt("print(speed)"),
    _stmt("R3 = R0:GetAttribute(\"Speed\")"),
    _stmt("print(R3)"),
], 0, ["print(speed)", "R3 = R0:GetAttribute(\"Speed\")", "print(R3)"])

_tree("keyword", [
    _stmt("R3 = R0:GetAttribute(\"type\")"),
    _stmt("print(R3)"),
], 1, ["kind = R0:GetAttribute(\"type\")", "print(kind)"])

_tree("hbind", [
    _stmt("R5 = R0:Connect(R1)"),
    Node("numfor", header="for R5 = R5, R0, R1 do", then_=[_stmt("print(R5)")]),
], 0, ["R5 = R0:Connect(R1)", "for R5 = R5, R0, R1 do", "  print(R5)", "end"])

_tree("loop-operands", [
    _stmt("R5 = 1"),
    _stmt("R3 = 4"),
    _stmt("R4 = 1"),
    Node("numfor", header="for i = R5, R3, R4 do", then_=[_stmt("print(i)")]),
], 3, ["start = 1", "limit = 4", "step = 1",
       "for i = start, limit, step do", "  print(i)", "end"])

_tree("loop-operand-conflict", [
    _stmt("R5 = 1"),
    Node("numfor", header="for i = R5, R3, R4 do", then_=[_stmt("print(i)")]),
    Node("numfor", header="for j = R6, R7, R5 do", then_=[_stmt("print(j)")]),
], 0, ["R5 = 1",
       "for i = R5, R3, R4 do", "  print(i)", "end",
       "for j = R6, R7, R5 do", "  print(j)", "end"])

_tree("loop-operand-suffix", [
    _stmt("R5 = 1"),
    _stmt("R3 = 4"),
    Node("numfor", header="for i = R5, R3, R4 do", then_=[_stmt("print(i)")]),
    _stmt("R6 = 1"),
    _stmt("R7 = 8"),
    Node("numfor", header="for j = R6, R7, R8 do", then_=[_stmt("print(j)")]),
], 4, ["start = 1", "limit = 4",
       "for i = start, limit, R4 do", "  print(i)", "end",
       "start2 = 1", "limit2 = 8",
       "for j = start2, limit2, R8 do", "  print(j)", "end"])

_tree("loop-operand-suffix3", [
    _stmt("R1 = 1"),
    Node("numfor", header="for i = R1, R2, R3 do", then_=[_stmt("print(i)")]),
    _stmt("R4 = 2"),
    Node("numfor", header="for j = R4, R5, R6 do", then_=[_stmt("print(j)")]),
    _stmt("R7 = 3"),
    Node("numfor", header="for k = R7, R8, R9 do", then_=[_stmt("print(k)")]),
], 3, ["start = 1", "for i = start, R2, R3 do", "  print(i)", "end",
       "start2 = 2", "for j = start2, R5, R6 do", "  print(j)", "end",
       "start3 = 3", "for k = start3, R8, R9 do", "  print(k)", "end"])

_tree("loop-operand-suffix-taken", [
    _stmt("print(start)"),
    _stmt("R5 = 1"),
    Node("numfor", header="for i = R5, R3, R4 do", then_=[_stmt("print(i)")]),
], 1, ["print(start)", "start2 = 1",
       "for i = start2, R3, R4 do", "  print(i)", "end"])

_tree("loop-operand-shared", [
    _stmt("R5 = 1"),
    Node("numfor", header="for i = R5, R3, R4 do", then_=[_stmt("print(i)")]),
    Node("numfor", header="for j = R5, R6, R7 do", then_=[_stmt("print(j)")]),
], 1, ["start = 1",
       "for i = start, R3, R4 do", "  print(i)", "end",
       "for j = start, R6, R7 do", "  print(j)", "end"])

_tree("loop-operand-use", [
    _stmt("R5 = 1"),
    Node("numfor", header="for i = R5, R3, R4 do", then_=[_stmt("print(i)")]),
    _stmt("print(R5)"),
], 0, ["R5 = 1", "for i = R5, R3, R4 do", "  print(i)", "end",
       "print(R5)"])

_tree("loop-operand-bind", [
    _stmt("R8 = 1"),
    Node("numfor", header="for R8 = R8, R6, R7 do", then_=[_stmt("print(R8)")]),
], 0, ["R8 = 1", "for R8 = R8, R6, R7 do", "  print(R8)", "end"])

_tree("cond-header", [
    _stmt("R5 = R0.Touched:Connect(R1)"),
    Node("if", cond=Cond("truthy", "R5"), then_=[_stmt("print(R5)")]),
    Node("genfor", header="for R13 in R5 do", then_=[_stmt("print(R13)")]),
], 2, ["touchedConnection = R0.Touched:Connect(R1)",
       "if touchedConnection then", "  print(touchedConnection)", "end",
       "for v in touchedConnection do", "  print(v)", "end"])

_tree("reg-new", [
    _stmt("R10 = R9.new('test')"),
    _stmt("print(R10)"),
], 0, ["R10 = R9.new('test')", "print(R10)"])

_tree("dupclosure", [
    _stmt("R3 = dupclosure 'fact'"),
    _stmt("print(R3)"),
], 1, ["fact = dupclosure 'fact'", "print(fact)"])

_tree("dupclosure-called", [
    _stmt("R5 = dupclosure 'nested_tern'(1, 2, 3)"),
    _stmt("print(R5)"),
], 0, ["R5 = dupclosure 'nested_tern'(1, 2, 3)", "print(R5)"])

_tree("strarg-service", [
    _stmt("R0 = game:GetService('Players')"),
    _stmt("print(R0)"),
], 1, ["players = game:GetService('Players')", "print(players)"])

_tree("pure-kind-str", [
    _stmt("R6 = tostring(R1)"),
    _stmt("print(R6)"),
], 1, ["str = tostring(R1)", "print(str)"])

_tree("pure-kind-kind", [
    _stmt("R5 = type(R0)"),
    _stmt("print(R5)"),
], 1, ["kind = type(R0)", "print(kind)"])

_tree("dotted-format", [
    _stmt("R6 = string.format('mode=%s', R0)"),
    _stmt("R2 = select('#')"),
    _stmt("R4 = assert(R5, 'x')"),
    _stmt("print(R6, R2, R4)"),
], 1, ["formatted = string.format('mode=%s', R0)", "R2 = select('#')",
       "R4 = assert(R5, 'x')", "print(formatted, R2, R4)"])

_tree("method-add-refuses", [
    _stmt("R11 = R8:add(7)"),
    _stmt("print(R11)"),
], 0, ["R11 = R8:add(7)", "print(R11)"])

_tree("method-upper-reg-refuses", [
    _stmt("R6 = R6:upper()"),
    _stmt("print(R6)"),
], 0, ["R6 = R6:upper()", "print(R6)"])

_tree("method-upper-recv", [
    _stmt("R6 = R0.name:upper()"),
    _stmt("print(R6)"),
], 1, ["name = R0.name:upper()", "print(name)"])

_tree("method-getter", [
    _stmt("R6 = R0:GetChildren()"),
    _stmt("print(R6)"),
], 1, ["children = R0:GetChildren()", "print(children)"])

_tree("method-participle", [
    _stmt("R6 = R0:sort()"),
    _stmt("print(R6)"),
], 1, ["sorted = R0:sort()", "print(sorted)"])

_tree("method-short-refuses", [
    _stmt("R6 = R0:do()"),
    _stmt("print(R6)"),
], 0, ["R6 = R0:do()", "print(R6)"])

_tree("dotted-abs", [
    _stmt("R2 = math.abs(R1)"),
    _stmt("print(R2)"),
], 1, ["abs = math.abs(R1)", "print(abs)"])

_tree("dotted-participle", [
    _stmt("R6 = table.concat(R5, ', ')"),
    _stmt("print(R6)"),
], 1, ["joined = table.concat(R5, ', ')", "print(joined)"])

_tree("dotted-verb-refuses", [
    _stmt("R6 = table.insert(R5, 1)"),
    _stmt("print(R6)"),
], 0, ["R6 = table.insert(R5, 1)", "print(R6)"])

_tree("dotted-reg-recv-refuses", [
    _stmt("R5 = R0.foo(1)"),
    _stmt("print(R5)"),
], 0, ["R5 = R0.foo(1)", "print(R5)"])

_tree("dotted-keyword-refuses", [
    _stmt("R5 = a.b.do()"),
    _stmt("print(R5)"),
], 0, ["R5 = a.b.do()", "print(R5)"])

_tree("dotted-web-split", [
    _stmt("R2 = math.abs(R1)"),
    _stmt("print(R2)"),
    _stmt("R2 = math.max(R1, 9)"),
    _stmt("print(R2)"),
], 2, ["abs = math.abs(R1)", "print(abs)",
       "max = math.max(R1, 9)", "print(max)"])

_tree("dotted-format-junk-arg", [
    _stmt("R5 = string.format('batch_init_%d', R2)"),
    _stmt("R6 = R0:Find('a.b')"),
    _stmt("print(R5, R6)"),
], 1, ["formatted = string.format('batch_init_%d', R2)", "R6 = R0:Find('a.b')",
       "print(formatted, R6)"])

_tree("return", [
    _stmt("R5 = R0:Connect(R1)"),
    Node("return", "return R5"),
], 1, ["connection = R0:Connect(R1)", "return connection"])

_tree("table-empty", [
    _stmt("R5 = {}"),
    _stmt("print(R5)"),
], 1, ["tbl = {}", "print(tbl)"])

_tree("table-collision", [
    _stmt("print(tbl)"),
    _stmt("R5 = {}"),
    _stmt("print(R5)"),
], 0, ["print(tbl)", "R5 = {}", "print(R5)"])

_tree("table-brace-string", [
    _stmt("R5 = '{' .. R0"),
    _stmt("print(R5)"),
], 0, ["R5 = '{' .. R0", "print(R5)"])

# -- spelling owner --
check("spell-lower", _S._sanitize("Speed") == "speed")
check("spell-acronym", _S._sanitize("ID") == "id")
check("spell-private", _S._sanitize("_balance") == "balance")
check("spell-keyword-pass", _S._sanitize("type") == "type")
check("spell-alt-kind", _S._binding_spelling("type") == "kind")
check("spell-alt-err", _S._binding_spelling("error") == "err")
check("spell-composed", _S._binding_spelling("types") == "types")
check("spell-counter", _S._suffixed("part", 2) == "part2")
check("spell-counter-digit", _S._suffixed("bit32", 2) == "bit32_2")
check("spell-preserve", _S._sanitize_preserve("Signal") == "Signal")
check("spell-camel", _S._lower_camel("GetAttribute") == "getAttribute")
check("spell-reglike", _S._sanitize("R1") is None and _S._sanitize("u12") is None)

_tree("genfor-pairs", [
    _stmt("R6, R7, R8 = pairs(R5)"),
    Node("genfor", header="for R9, R10 in R6, R7, R8 do",
         then_=[_stmt("print(R9, R10)")]),
], 5, ["pairsFn, pairsTbl, pairsKey = pairs(R5)",
       "for k, v in pairsFn, pairsTbl, pairsKey do", "  print(k, v)",
       "end"])

_tree("genfor-triple-suffix", [
    _stmt("R1, R2, R3 = pairs(R0)"),
    Node("genfor", header="for R9, R10 in R1, R2, R3 do",
         then_=[_stmt("print(R9)")]),
    _stmt("R4, R5, R6 = pairs(R0)"),
    Node("genfor", header="for R11, R12 in R4, R5, R6 do",
         then_=[_stmt("print(R11)")]),
], 10, ["pairsFn, pairsTbl, pairsKey = pairs(R0)",
       "for k, v in pairsFn, pairsTbl, pairsKey do", "  print(k)",
       "end", "pairsFn2, pairsTbl2, pairsKey2 = pairs(R0)",
       "for k2, v2 in pairsFn2, pairsTbl2, pairsKey2 do",
       "  print(k2)", "end"])

_tree("genfor-var-suffix-taken", [
    _stmt("print(k)"),
    Node("genfor", header="for R5, R6 in R1, R2, R3 do",
         then_=[_stmt("print(R5, R6)")]),
], 2, ["print(k)", "for k2, v in R1, R2, R3 do", "  print(k2, v)",
       "end"])

_tree("genfor-var-suffix-claimed", [
    Node("genfor", header="for R5, R6 in R1, R2, R3 do",
         then_=[_stmt("print(R5)")]),
    Node("genfor", header="for R7, R8 in R1, R2, R3 do",
         then_=[_stmt("print(R7)")]),
], 4, ["for k, v in R1, R2, R3 do", "  print(k)", "end",
       "for k2, v2 in R1, R2, R3 do", "  print(k2)", "end"])

_tree("genfor-triple-wrongslot", [
    _stmt("R1, R2, R3 = pairs(R0)"),
    Node("genfor", header="for R9, R10 in R2, R1, R3 do",
         then_=[_stmt("print(R9)")]),
], 3, ["R1, R2, pairsKey = pairs(R0)",
       "for k, v in R2, R1, pairsKey do", "  print(k)", "end"])

_tree("genfor-single-var", [
    _stmt("R5, R6, R7 = R3(1)"),
    Node("genfor", header="for R8 in R5, R6, R7 do",
         then_=[_stmt("print(R8)")]),
], 1, ["R5, R6, R7 = R3(1)", "for v in R5, R6, R7 do",
       "  print(v)", "end"])

_tree("genfor-nested-store", [
    _stmt("R6, R7, R8 = pairs(R5)"),
    Node("genfor", header="for R9, R10 in R6, R7, R8 do",
         then_=[_stmt("R6 = 1"), _stmt("print(R9)")]),
], 4, ["R6, pairsTbl, pairsKey = pairs(R5)",
       "for k, v in R6, pairsTbl, pairsKey do", "  R6 = 1",
       "  print(k)", "end"])

_tree("genfor-nested-triple", [
    _stmt("R0 = 1"),
    Node("if", cond=Cond("truthy", "R0"),
         then_=[_stmt("R1, R2, R3 = pairs(R0)"),
                Node("genfor", header="for R4, R5 in R1, R2, R3 do",
                     then_=[_stmt("print(R4)")])]),
], 2, ["R0 = 1", "if R0 then", "  R1, R2, R3 = pairs(R0)",
       "  for k, v in R1, R2, R3 do", "    print(k)", "  end", "end"])

_tree("genfor-body-top", [
    Node("genfor", header="for R5, R6 in R1, R2, R3 do",
         then_=[_stmt("R5..top = 1"), _stmt("print(R6)")]),
], 1, ["for R5, v in R1, R2, R3 do", "  R5..top = 1",
       "  print(v)", "end"])

_tree("genfor-var-conflict", [
    _stmt("R1, R2, R3 = pairs(R0)"),
    Node("genfor", header="for R5, R6 in R1, R2, R3 do",
         then_=[_stmt("print(R5)")]),
    Node("genfor", header="for R6, R7 in R1, R2, R3 do",
         then_=[_stmt("print(R6)")]),
], 5, ["pairsFn, pairsTbl, pairsKey = pairs(R0)",
       "for k, R6 in pairsFn, pairsTbl, pairsKey do", "  print(k)",
       "end", "for R6, v in pairsFn, pairsTbl, pairsKey do",
       "  print(R6)", "end"])

_tree("genfor-var-dupbind", [
    Node("genfor", header="for R5, R5 in R1, R2, R3 do",
         then_=[_stmt("print(R5)")]),
], 0, ["for R5, R5 in R1, R2, R3 do", "  print(R5)", "end"])

_tree("accum-total", [
    _stmt("R0 = 0"),
    Node("while", cond=Cond("truthy", "R7"), then_=[_stmt("R0 += R1")]),
    Node("return", "return R0"),
], 1, ["total = 0", "while R7 do", "  total += R1", "end",
       "return total"])

_tree("accum-count", [
    _stmt("R0 = 0"),
    Node("while", cond=Cond("truthy", "R7"), then_=[_stmt("R0 += 1")]),
    Node("return", "return R0"),
], 1, ["count = 0", "while R7 do", "  count += 1", "end",
       "return count"])

_tree("accum-buf", [
    _stmt('R5 = ""'),
    Node("while", cond=Cond("truthy", "R7"), then_=[_stmt("R5 ..= R1")]),
    Node("return", "return R5"),
], 1, ["buf = \"\"", "while R7 do", "  buf ..= R1", "end",
       "return buf"])

_tree("accum-redef-bounds", [
    _stmt("R0 = 0"),
    Node("while", cond=Cond("truthy", "R7"), then_=[_stmt("R0 += R1")]),
    _stmt("print(R0)"),
    _stmt("R0 = 5"),
    Node("return", "return R0"),
], 1, ["total = 0", "while R7 do", "  total += R1", "end",
       "print(total)", "R0 = 5", "return R0"])

_tree("accum-nested-store", [
    _stmt("R0 = 0"),
    Node("while", cond=Cond("truthy", "R7"),
         then_=[_stmt("R0 += 1"),
                Node("if", cond=Cond("truthy", "R8"),
                     then_=[_stmt("R0 = 2")])]),
    Node("return", "return R0"),
], 0, ["R0 = 0", "while R7 do", "  R0 += 1", "  if R8 then",
       "    R0 = 2", "  end", "end", "return R0"])

_tree("accum-dead-store", [
    _stmt("R0 = 0"),
    Node("while", cond=Cond("truthy", "R7"), then_=[_stmt("R0 += R1")]),
], 0, ["R0 = 0", "while R7 do", "  R0 += R1", "end"])

_tree("accum-bare-constant", [
    _stmt("R0 = 0"),
    Node("return", "return R0"),
], 0, ["R0 = 0", "return R0"])

_tree("accum-suffix-taken", [
    _stmt("print(total)"),
    _stmt("R0 = 0"),
    Node("while", cond=Cond("truthy", "R7"), then_=[_stmt("R0 += R1")]),
    Node("return", "return R0"),
], 1, ["print(total)", "total2 = 0", "while R7 do",
       "  total2 += R1", "end", "return total2"])

_tree("genfor-var-taken", [
    _stmt("print(k)"),
    _stmt("R6, R7, R8 = pairs(R5)"),
    Node("genfor", header="for R9, R10 in R6, R7, R8 do",
         then_=[_stmt("print(R9, R10)")]),
], 5, ["print(k)", "pairsFn, pairsTbl, pairsKey = pairs(R5)",
       "for k2, v in pairsFn, pairsTbl, pairsKey do",
       "  print(k2, v)", "end"])

# -- corpus proof: names-mode must fire on real fixtures, not just synthetics --
import glob as _glob
import re as _re
_corpus_renamed = 0
_corpus_roles = 0
_corpus_triples = 0
_corpus_genvars = 0
_corpus_accums = 0
_corpus_dotted = 0
for _f in sorted(_glob.glob("fixtures/v14/**/*.luac", recursive=True)):
    try:
        _bc = parser_v14.parse(open(_f, "rb").read())
    except Exception:
        continue
    for _p in _bc.protos:
        try:
            _r = Structurer(_bc, _p, names=True).structure()
        except Exception:
            continue
        _corpus_renamed += _r.stats.get("renamed", 0)
        _corpus_roles += sum(
            1 for _ln in render_all(_r.nodes)
            if _re.match(r"^(start|limit|step)[0-9]* = ", _ln.strip()))
        _corpus_triples += sum(
            1 for _ln in render_all(_r.nodes)
            if _re.match(r"^(?:pairs|ipairs|next)Fn\d*, "
                         r"(?:pairs|ipairs|next)Tbl\d*, "
                         r"(?:pairs|ipairs|next)Key\d* = ", _ln.strip()))
        _corpus_genvars += sum(
            1 for _ln in render_all(_r.nodes)
            if _re.match(r"^for k\d*, v\d* in ", _ln.strip())
            or _re.match(r"^for v\d* in ", _ln.strip()))
        _corpus_accums += sum(
            1 for _ln in render_all(_r.nodes)
            if _re.match(r"^(total|count|buf)[0-9]* = ", _ln.strip()))
        _corpus_dotted += sum(
            1 for _ln in render_all(_r.nodes)
            if _re.match(r"^(abs|max|min|floor|ceil|sqrt|sin|cos|rad|len|"
                         r"unpack|band|bor|bxor|clamped|formatted|"
                         r"magnitude) = ", _ln.strip()))
_tree("coerce-field", [
    _stmt("R5 = tostring(R0.v)"),
    _stmt("print(R5)"),
], 1, ["v = tostring(R0.v)", "print(v)"])

_tree("coerce-evidence", [
    _stmt("R5 = tostring(R0:GetAttribute(\"Speed\"))"),
    _stmt("print(R5)"),
], 1, ["speed = tostring(R0:GetAttribute(\"Speed\"))", "print(speed)"])

_tree("coerce-nested", [
    _stmt("R5 = tostring(tostring(R0.v))"),
    _stmt("print(R5)"),
], 1, ["str = tostring(tostring(R0.v))", "print(str)"])

_tree("param-slot", [
    _stmt("R0 = game:GetService('Players')"),
    _stmt("print(R0)"),
], 0, ["R0 = game:GetService('Players')", "print(R0)"], numparams=1)

_tree("web-split", [
    _stmt("R6 = tostring(R1)"),
    _stmt("R4 = R5 .. R6"),
    _stmt("R6 = R2(1)"),
    _stmt("print(R6)"),
], 1, ["str = tostring(R1)", "R4 = R5 .. str", "R6 = R2(1)", "print(R6)"])

_tree("web-nested-refuses", [
    _stmt("R6 = tostring(R1)"),
    Node("if", cond=Cond("truthy", "R6"), then_=[_stmt("print(R6)")]),
    _stmt("R6 = R2(1)"),
    _stmt("print(R6)"),
], 0, ["R6 = tostring(R1)", "if R6 then", "  print(R6)", "end",
       "R6 = R2(1)", "print(R6)"])

_tree("web-predef-use", [
    _stmt("print(R6)"),
    _stmt("R6 = tostring(R1)"),
    _stmt("print(R6)"),
    _stmt("R6 = R2(1)"),
    _stmt("print(R6)"),
], 1, ["print(R6)", "str = tostring(R1)", "print(str)", "R6 = R2(1)",
       "print(R6)"])

_tree("web-interleaved", [
    _stmt("R1 = tostring(R0.v)"),
    _stmt("R2 = R0:Clone()"),
    _stmt("print(R1)"),
    _stmt("R1 = R3(1)"),
    _stmt("print(R1, R2)"),
], 2, ["v = tostring(R0.v)", "clone = R0:Clone()", "print(v)",
       "R1 = R3(1)", "print(R1, clone)"])

_tree("web-return", [
    _stmt("R6 = tostring(R1)"),
    _stmt("print(R6)"),
    _stmt("R6 = R2(1)"),
    Node("return", "return R6"),
], 1, ["str = tostring(R1)", "print(str)", "R6 = R2(1)",
       "return R6"])

_tree("web-goto", [
    _stmt("R6 = tostring(R1)"),
    Node("goto", "goto L0"),
    _stmt("R6 = R2(1)"),
    Node("label", "L0:"),
    _stmt("print(R6)"),
], 1, ["str = tostring(R1)", "goto L0", "R6 = R2(1)", "L0:",
       "print(R6)"])

_tree("upval-debug", [
    _stmt("R5 = U0"),
    _stmt("print(R5)"),
], 1, ["seed = U0", "print(seed)"], upvals=["seed"])

_tree("upval-annot", [
    _stmt("R5 = U0(seed)"),
    _stmt("print(R5)"),
], 1, ["seed = U0(seed)", "print(seed)"])

_tree("upval-annotation-not-taken", [
    _stmt("R5 = U0(seed)"),
    _stmt("R6 = U1"),
    _stmt("print(R5, R6)"),
], 1, ["seed = U0(seed)", "R6 = U1", "print(seed, R6)"])

_tree("upval-stripped-refuses", [
    _stmt("R5 = U0"),
    _stmt("print(R5)"),
], 0, ["R5 = U0", "print(R5)"])

_tree("upval-range-refuses", [
    _stmt("R5 = U9"),
    _stmt("print(R5)"),
], 0, ["R5 = U9", "print(R5)"], upvals=["seed"])

_tree("upval-collision", [
    _stmt("print(seed)"),
    _stmt("R5 = U0"),
    _stmt("print(R5)"),
], 0, ["print(seed)", "R5 = U0", "print(R5)"], upvals=["seed"])

_tree("upval-coerce", [
    _stmt("R5 = tostring(U0)"),
    _stmt("print(R5)"),
], 1, ["seed = tostring(U0)", "print(seed)"], upvals=["seed"])

# E2E through the real Structurer: hand-encoded GETUPVAL with proto debug
# upvalue names (all checked-in fixtures are compiled stripped, so the
# corpus cannot prove this path).
def _run_upvals(words, consts, upvals, names):
    p = Proto(id=0, maxstack=12, numparams=0, code=list(words),
              consts=list(consts), debugname=b"t", upvals=list(upvals))
    bc = Bytecode(version=14, protos=[p], mainid=0)
    r = Structurer(bc, p, names=names).structure()
    return r.stats.get("renamed", 0), render_all(r.nodes)

# The cond keeps R5's def live: JUMPIF reads the register verbatim
# (conds never fold), unlike CALL/RETURN uses which the lifter folds.
_upv_words = [_abc("GETUPVAL", 5, 0), _ad("JUMPIFNOT", 5, 1),
              _abc("RETURN", 5, 2), _abc("RETURN", 0, 1)]
_n0, _off = _run_upvals(_upv_words, [], ["seed"], False)
_n1, _on = _run_upvals(_upv_words, [], ["seed"], True)
check("e2e-upval",
      (_n0, _off) == (0, ["R5 = U0(seed)", "if not R5 then", "  return",
                          "end", "return U0(seed)"])
      and _on == ["seed = U0(seed)", "if not seed then", "  return",
                  "end", "return U0(seed)"] and _n1 == 1)
if (_n0, _off) != (0, ["R5 = U0(seed)", "if not R5 then", "  return",
                        "end", "return U0(seed)"]):
    print("  off: ", _off)
if _on != ["seed = U0(seed)", "if not seed then", "  return",
            "end", "return U0(seed)"] or _n1 != 1:
    print("  on:  ", _on, "renamed=", _n1)

_tree("selfread-refuses", [
    _stmt("R5 = tostring(R5)"),
    _stmt("print(R5)"),
], 0, ["R5 = tostring(R5)", "print(R5)"])

_tree("region-loop", [
    _stmt("R6 = R0 .. R1"),
    Node("while", cond=Cond("truthy", "R7"),
         then_=[_stmt("R6 = {}"), _stmt("print(R6)")]),
    _stmt("print(R0)"),
], 1, ["R6 = R0 .. R1", "while R7 do", "  tbl = {}", "  print(tbl)",
       "end", "print(R0)"])

_tree("region-loop-entry-refuses", [
    _stmt("R6 = R0 .. R1"),
    Node("while", cond=Cond("truthy", "R7"),
         then_=[_stmt("print(R6)"), _stmt("R6 = {}"),
                 _stmt("print(R6)")]),
    _stmt("print(R0)"),
], 0, ["R6 = R0 .. R1", "while R7 do", "  print(R6)", "  R6 = {}",
       "  print(R6)", "end", "print(R0)"])

_tree("region-loop-cond-refuses", [
    _stmt("R6 = R0 .. R1"),
    Node("while", cond=Cond("truthy", "R6"),
         then_=[_stmt("R6 = {}"), _stmt("print(R6)")]),
    _stmt("print(R0)"),
], 0, ["R6 = R0 .. R1", "while R6 do", "  R6 = {}", "  print(R6)",
       "end", "print(R0)"])

_tree("region-loop-suffix-refuses", [
    _stmt("R6 = R0 .. R1"),
    Node("while", cond=Cond("truthy", "R7"),
         then_=[_stmt("R6 = {}"), _stmt("print(R6)")]),
    _stmt("print(R6)"),
], 0, ["R6 = R0 .. R1", "while R7 do", "  R6 = {}", "  print(R6)",
       "end", "print(R6)"])

_tree("region-loop-twodefs-refuses", [
    _stmt("R6 = R0 .. R1"),
    Node("while", cond=Cond("truthy", "R7"),
         then_=[_stmt("R6 = {}"), _stmt("print(R6)"),
                 _stmt("R6 = {1}"), _stmt("print(R6)")]),
    _stmt("print(R0)"),
], 0, ["R6 = R0 .. R1", "while R7 do", "  R6 = {}", "  print(R6)",
       "  R6 = {1}", "  print(R6)", "end", "print(R0)"])

_tree("region-branch", [
    _stmt("R6 = R0 .. R1"),
    Node("if", cond=Cond("truthy", "R7"), then_=[_stmt("R6 = {}"),
         _stmt("print(R6)")], else_=[_stmt("print(1)")]),
    _stmt("print(R0)"),
], 1, ["R6 = R0 .. R1", "if R7 then", "  tbl = {}", "  print(tbl)",
       "else", "  print(1)", "end", "print(R0)"])

_tree("region-branch-suffix-refuses", [
    _stmt("R6 = R0 .. R1"),
    Node("if", cond=Cond("truthy", "R7"), then_=[_stmt("R6 = {}"),
         _stmt("print(R6)")], else_=[_stmt("print(1)")]),
    _stmt("print(R6)"),
], 0, ["R6 = R0 .. R1", "if R7 then", "  R6 = {}", "  print(R6)",
       "else", "  print(1)", "end", "print(R6)"])

_tree("region-order-stable", [
    _stmt("R2 = {}"),
    Node("while", cond=Cond("truthy", "R7"),
         then_=[_stmt("R11 = {x = 0}"), _stmt("print(R11)")]),
    Node("return", "return R2"),
], 1, ["tbl = {}", "while R7 do", "  R11 = {x = 0}",
       "  print(R11)", "end", "return tbl"])

check("corpus-fires", _corpus_renamed >= 20)
print("corpus renamed: %d" % _corpus_renamed)
check("corpus-roles", _corpus_roles >= 26)
print("corpus role-names: %d" % _corpus_roles)
check("corpus-triples", _corpus_triples >= 7)
print("corpus triples: %d" % _corpus_triples)
check("corpus-genvars", _corpus_genvars >= 11)
print("corpus genvars: %d" % _corpus_genvars)
check("corpus-accums", _corpus_accums >= 23)
print("corpus accums: %d" % _corpus_accums)
check("corpus-dotted", _corpus_dotted >= 7)
print("corpus dotted: %d" % _corpus_dotted)

print("failures: %d" % len(fails))
sys.exit(1 if fails else 0)
