"""Value/expression sweeps: boolean merging and its shared single-assign gate, nil-filler absorption, if-expression compaction, and the tiny expression polishes (spell, interpolate, flatten-concat)."""
from __future__ import annotations

import re
import struct

from struct_node import Node


class ValueMixin:
    """Value/expression sweeps: boolean merging and its shared single-assign gate, nil-filler absorption, if-expression compaction, and the tiny expression polishes (spell, interpolate, flatten-concat)."""

    _BOOL_MERGE_VAL = re.compile(r"^R(\d+) = (\S(?:.*\S)?)$")
    def _single_assign_pair(self, nd: Node):
        """Shared shape gate for the if/else-assign folders: `if C then
        R = X else R = Y end` with single pure assigns to the same
        register. Returns (reg, then-val, else-val) or None."""
        if nd.kind != "if" or nd.cond is None:
            return None
        if len(nd.then_) != 1 or len(nd.else_) != 1:
            return None
        t, e = nd.then_[0], nd.else_[0]
        if t.kind != "stmt" or e.kind != "stmt" or not t.pure or not e.pure:
            return None
        if t.defreg is None or t.defreg != e.defreg:
            return None
        mt = self._BOOL_MERGE_VAL.match(t.text.strip())
        me = self._BOOL_MERGE_VAL.match(e.text.strip())
        if not mt or not me or mt.group(1) != me.group(1):
            return None
        return mt.group(1), mt.group(2), me.group(2)
    def _merge_bool_assigns(self, lst: list[Node]) -> int:
        """Fold `if C then R = X else R = Y end` (single pure assigns to the
        same register) into one boolean expression, mirroring the reference
        Compound_Boolean_Expression_Structurer: complementary boolean
        constants merge to `C` / `not C`, and a `false` arm folds to `and`.
        Only value-preserving shapes (no truthiness assumptions):
          (true,false)->C  (false,true)->not C
          (v,false)->`C and v`  (false,v)->`(not C) and v`
        `v` must be pure-shaped (no calls); the condition text is reused
        verbatim. Recurses into branches/loops; returns merge count."""
        merged = 0
        for nd in lst:
            if nd.then_:
                merged += self._merge_bool_assigns(nd.then_)
            if nd.else_:
                merged += self._merge_bool_assigns(nd.else_)
        for i, nd in enumerate(lst):
            pair = self._single_assign_pair(nd)
            if pair is None:
                continue
            reg, tv, ev = pair
            if "(" in tv or "(" in ev:  # calls/closures: side effects, keep split
                continue
            c = nd.cond.render()
            if tv == "true" and ev == "false":
                expr = f"R{reg} = {c}"
            elif tv == "false" and ev == "true":
                expr = f"R{reg} = not ({c})"
            elif ev == "false":
                expr = f"R{reg} = ({c}) and ({tv})"
            elif tv == "false":
                expr = f"R{reg} = (not ({c})) and ({ev})"
            else:
                continue
            lst[i] = Node("stmt", expr, pure=True, defreg=nd.then_[0].defreg)
            merged += 1
        return merged
    _NIL_STORE_RE = re.compile(r"^R(\d+) = nil$")
    _GENFOR_RE = re.compile(r"^for\s+(R\d+(?:, R\d+)*)\s+in\s+(.+?)\s*do$")
    def _eliminate_nil(self, lst: list[Node], roots: list[Node] | None = None) -> int:
        """Absorb trailing `R = nil` fillers into generic-for headers:
        `R11 = nil; R12 = nil; for R13 in R10, R11, R12 do` becomes
        `for R13 in R10 do` (missing iterator state/control default to
        nil, so the reordered form is exact). Only trailing operands go:
        the function operand is never touched. Gate: the store must be
        adjacent and pure, the filler must not be a bound loop var, and it
        must be unread everywhere else (whole-function rhs/cond/header/
        return scan) -- in particular this refuses fillers read after
        the loop, where the
        original loop's control writes would be observable. The header is
        rewritten first and restored on gate failure; each firing deletes
        a store, so the fixpoint cannot ping-pong."""
        if roots is None:
            roots = lst
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._eliminate_nil(nd.then_, roots)
            if nd.else_:
                n += self._eliminate_nil(nd.else_, roots)
        i = 0
        while i < len(lst):
            nd = lst[i]
            if nd.kind == "genfor" and nd.header:
                gm = self._GENFOR_RE.match(nd.header)
                if gm:
                    bound = {int(x[1:]) for x in gm.group(1).split(", ")}
                    ops = [o.strip() for o in gm.group(2).split(",")]
                    while (len(ops) > 1 and i > 0
                           and (om := self._NIL_STORE_RE.match(
                               lst[i - 1].text.strip())) is not None
                           and lst[i - 1].kind == "stmt"
                           and lst[i - 1].pure
                           and int(om.group(1)) == self._operand_reg(ops[-1])
                           and int(om.group(1)) not in bound):
                        reg = int(om.group(1))
                        old_header = nd.header
                        nd.header = (f"for {gm.group(1)} in "
                                     f"{', '.join(ops[:-1])} do")
                        if not self._occurs(roots, reg, roles=("rhs", "cond",
                                                              "header", "ret")):
                            del lst[i - 1]
                            i -= 1
                            ops = ops[:-1]
                            n += 1
                        else:
                            nd.header = old_header
                            break
            i += 1
        return n
    @staticmethod
    def _operand_reg(operand: str) -> int | None:
        m = re.fullmatch(r"R(\d+)", operand.strip())
        return int(m.group(1)) if m else None
    _IFEXPR_MAX_WIDTH = 90  # longer than this stays an if statement
    def _compact_conditionals(self, lst: list[Node]) -> int:
        """Fold `if C then R = X else R = Y end` (single pure assigns to
        the same register, non-boolean arms bool-merge declines) into a
        Luau if-expression: `R = if C then X else Y`. Sound: the cond is
        evaluated once and exactly one arm runs in both forms; pure arms
        have no ordering hazards. Skips identical arms (bool-merge's
        unconditional-assign territory), call-shaped arms (conservative,
        mirroring bool-merge), and results over the width cap. Output is a
        plain stmt, so the fixpoint cannot ping-pong."""
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._compact_conditionals(nd.then_)
            if nd.else_:
                n += self._compact_conditionals(nd.else_)
        for i, nd in enumerate(lst):
            pair = self._single_assign_pair(nd)
            if pair is None:
                continue
            reg, tv, ev = pair
            if tv == ev:  # identical arms: unconditional assign, not ours
                continue
            if "(" in tv or "(" in ev:  # calls: keep split, like bool-merge
                continue
            expr = f"R{reg} = if {nd.cond.render()} then {tv} else {ev}"
            if len(expr) > self._IFEXPR_MAX_WIDTH:
                continue
            lst[i] = Node("stmt", expr, pure=True, defreg=nd.then_[0].defreg)
            n += 1
        return n
    _FLOAT_LIT_RE = re.compile(r"(?<![\w.])(\d+\.\d+(?:[eE][+-]?\d+)?)(?![\w.])")
    _FORMAT_CALL_RE = re.compile(r"^R(\d+) = \('((?:[^'\\]|\\.)*)'\):format\((.*)\)$")
    @staticmethod
    def _exact_fraction(value: float):
        """`(p, q)` with `p / q` bit-equal to a long decimal float and
        shorter than it (Tovek spell_constants: IEEE division is correctly
        rounded, numbers have no metamethods, so the fraction is exact).
        Lowest-terms convergent, denominator <= 1000, |p| < 2^31; -0,
        infinities and NaN never spell, and short/exponent decimals stay."""
        import math
        from fractions import Fraction
        if not math.isfinite(value) or value == 0.0:
            return None
        mag = abs(value)
        if not (1.0 / 1000 <= mag < float(1 << 31)) or mag == int(mag):
            return None
        # continued-fraction convergents of the exact binary value; any
        # bit-equal p/q is one, so the first hit has the smallest denominator
        fr = Fraction(mag)
        n, d = fr.numerator, fr.denominator
        p_prev, p, q_prev, q = 0, 1, 1, 0
        while d:
            a, r = divmod(n, d)
            n, d = d, r
            p_prev, p = p, a * p + p_prev
            q_prev, q = q, a * q + q_prev
            if q > 1000 or p >= (1 << 31):
                return None
            if struct.pack("<d", p / q) == struct.pack("<d", mag):
                return (-p if value < 0 else p), q
        return None
    def _spell_constants(self, lst: list[Node]) -> int:
        """Rewrite long float decimals as bit-equal `(p / q)` when shorter
        (Tovek spell_constants). Quote/comment-aware: string contents and
        trailing `--` notes are never touched; only stmt value texts."""
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._spell_constants(nd.then_)
            if nd.else_:
                n += self._spell_constants(nd.else_)
            if nd.kind != "stmt" or not nd.text:
                continue
            code, comment = self._code_part(nd.text)
            def sub(m):
                lit = m.group(1)
                if "e" in lit or "E" in lit:
                    return lit  # exponent form: leave alone
                digits = lit.replace(".", "").lstrip("0")
                if len(digits) < 10:
                    return lit
                try:
                    frac = self._exact_fraction(float(lit))
                except Exception:
                    return lit
                if frac is None:
                    return lit
                spelled = f"({frac[0]} / {frac[1]})"
                return spelled if len(spelled) < len(lit) else lit
            # substitute per unquoted run: string contents are never floats
            new_code = "".join(self._FLOAT_LIT_RE.sub(sub, r) if not q else r
                                for r, q in self._quoted_runs(code))
            # a constant that was already a call arg nests parens: `((p / q))`
            new_code = re.sub(r"\(\((-?\d+ / \d+)\)\)", r"(\1)", new_code)
            if new_code != code:
                nd.text = new_code + (" " + comment if comment else "")
                n += 1
        return n
    def _interpolate_format(self, lst: list[Node]) -> int:
        """Fold `R = ('...%*...'):format(a, ...)` into a Luau backtick
        string `` `...{a}...` `` (Tovek interpolation). Gate: only `%*`
        (and `%%`) specs, arity match, single-value args (no spreading
        last arg, no vararg/multret), no braces/backtick in static text
        or args, no `--` in args. Pure-status recomputed from the new
        text (no parens means no calls). The stale `-- format method`
        note is dropped."""
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._interpolate_format(nd.then_)
            if nd.else_:
                n += self._interpolate_format(nd.else_)
            if nd.kind != "stmt" or not nd.text:
                continue
            code, _ = self._code_part(nd.text)
            m = self._FORMAT_CALL_RE.match(code.strip())
            if not m:
                continue
            reg, body, argstr = m.group(1), m.group(2), m.group(3)
            args = [a.strip() for a in self._split_top(argstr, ",")]
            if any(not a or "{" in a or "}" in a or "`" in a or "--" in a
                   or "..." in self._unquoted(a) or "..top" in a
                   or "multret" in a for a in args):
                continue
            out, i, holes = [], 0, 0
            bad = False
            while i < len(body):
                ch = body[i]
                if ch == "\\" and i + 1 < len(body):
                    out.append(body[i:i + 2])
                    i += 2
                    continue
                if ch == "%" and i + 1 < len(body):
                    nx = body[i + 1]
                    if nx == "%":
                        out.append("%")
                        i += 2
                        continue
                    if nx == "*":
                        holes += 1
                        if holes > len(args):
                            bad = True
                            break
                        out.append("{" + args[holes - 1] + "}")
                        i += 2
                        continue
                    bad = True
                    break
                if ch in "`{}":
                    bad = True
                    break
                out.append(ch)
                i += 1
            if bad or holes != len(args):
                continue
            expr = f"R{reg} = `" + "".join(out) + "`"
            nd.text = expr
            nd.pure = "(" not in expr
            n += 1
        return n
    def _flatten_concat(self, lst: list[Node]) -> int:
        """Re-associate left-nested concat walls `((a .. b) .. c) .. d`
        into flat `a .. b .. c .. d` (Tovek rebalance_expressions, which
        only splits 4+-operator spines into accumulators -- our two walls
        are 3-op, so paren-flattening is the honest fix). Gate: every leaf
        call-free (no parens) and vararg-free, so operand order and values
        are unchanged; regrouping is unobservable for the string operands
        these walls carry (theoretical `__concat` reorder accepted, same
        as the reference's own regroup). Only fires when parens drop."""
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._flatten_concat(nd.then_)
            if nd.else_:
                n += self._flatten_concat(nd.else_)
            if nd.kind != "stmt" or not nd.text or ".." not in nd.text:
                continue
            if "..top" in nd.text or "multret" in nd.text:
                continue
            code, comment = self._code_part(nd.text)
            if "..=" in code:  # compound tail: not a plain def, leave it
                continue
            parts = self._split_lhs_rhs(code)
            if parts is None:
                continue
            lhs, rhs = parts
            if ".." not in rhs:
                continue
            leaves = self._flat_leaves(rhs)
            if (len(leaves) < 2
                    or any("(" in self._unquoted(lf)
                           or "..." in self._unquoted(lf) for lf in leaves)):
                continue
            flat = " .. ".join(leaves)
            if flat == rhs.strip():
                continue
            nd.text = f"{lhs} = {flat}" + (" " + comment if comment else "")
            n += 1
        return n
    def _flat_leaves(self, rhs: str) -> list[str]:
        """Top-level `..` operands with fully-wrapping redundant parens
        stripped (recursively)."""
        out = []
        for part in self._split_top(rhs, ".."):
            part = part.strip()
            if (len(part) >= 2 and part[0] == "(" and part[-1] == ")"
                    and len(self._split_top(part, "..")) == 1
                    and len(self._split_top(part[1:-1], "..")) > 1):
                out.extend(self._flat_leaves(part[1:-1]))
            else:
                out.append(part)
        return out
