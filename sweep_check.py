"""Regression test for register copy sweeps (struct_sweep_reg.RegMixin).

Tree cases through _propagate_copies directly (pure tree logic, no
bytecode needed -- same pattern as naming_check's _tree cases):

1. A copy feeding both a stmt use and `return Rdst` must be KEPT
   (the rewriter leaves returns alone; propagating would leave
   `return Rdst` reading a deleted alias).
2. A dead copy in a straight-line return-terminated list is dropped
   (nothing past it can observe the value: enclosing tails and loop
   back-edges are unreachable past `return`).
3. Refusals: later cond read, break-terminated list (enclosing tails
   reachable), later compound store (a def+read).

Run: python sweep_check.py. Exit 1 with FAIL lines on any mismatch.
"""
import sys

sys.path.insert(0, ".")
from struct_node import Node, Cond
from struct_v14 import Structurer

fails = []


def check(title, cond):
    print(("PASS " if cond else "FAIL ") + title)
    if not cond:
        fails.append(title)


_S = Structurer.__new__(Structurer)


def _run(nodes):
    _S._nodes = nodes
    n = _S._propagate_copies(nodes)
    return n, [(nd.kind, nd.text) for nd in nodes]


def _case(title, nodes, want_n, want):
    n, got = _run(nodes)
    kinds = [g[0] for g in got]
    texts = [g[1] for g in got]
    ok = n == want_n and kinds == [k for k, _ in want] \
        and texts == [t for _, t in want]
    check(title, ok)
    if not ok:
        print("  want:", want)
        print("  got: ", list(zip(kinds, texts)), "n=%d" % n)


# Copy feeding a stmt use AND a return use: the return is outside the
# rewriter's reach, so the copy must stay (else `return R1` dangles).
_case("copy-ret-kept", [
    Node("stmt", "R1 = R0"),
    Node("stmt", "print(R1)"),
    Node("return", "return R1"),
], 0, [("stmt", "R1 = R0"), ("stmt", "print(R1)"),
       ("return", "return R1")])

# Plain propagation still fires when every read is rewritten.
_case("copy-basic", [
    Node("stmt", "R1 = R0"),
    Node("stmt", "print(R1)"),
    Node("stmt", "print(R0)"),
], 1, [("stmt", "print(R0)"), ("stmt", "print(R0)")])

# Dead copy in a straight-line return-terminated list: dropped.
_case("copy-dead", [
    Node("stmt", "R1 = R0"),
    Node("stmt", "print(1)"),
    Node("return", "return R0"),
], 1, [("stmt", "print(1)"), ("return", "return R0")])

# Dead copy with a later cond read: kept.
_case("copy-dead-cond-refuses", [
    Node("stmt", "R1 = R0"),
    Node("if", cond=Cond("truthy", "R1"), then_=[Node("stmt", "print(1)")]),
    Node("return", "return R0"),
], 0, [("stmt", "R1 = R0"), ("if", ""),
       ("return", "return R0")])

# Copy in a break-terminated list: enclosing tails stay reachable past
# `break`, so a dead-looking copy is kept.
_case("copy-dead-break-refuses", [
    Node("while", cond=Cond("truthy", "R7"), then_=[
        Node("stmt", "R1 = R0"),
        Node("stmt", "print(1)"),
        Node("break", "break")]),
], 0, [("while", "")])

# Dead copy with a later compound store: kept (compound reads the value).
_case("copy-dead-compound-refuses", [
    Node("stmt", "R1 = R0"),
    Node("stmt", "R1 += 1"),
    Node("return", "return R0"),
], 0, [("stmt", "R1 = R0"), ("stmt", "R1 += 1"),
       ("return", "return R0")])

print("failures: %d" % len(fails))
sys.exit(1 if fails else 0)
