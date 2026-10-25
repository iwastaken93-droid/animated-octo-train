"""Shared sweep utilities: single owners of the occurrence query (_occurs),
the quote/comment-aware text scanners, the assignment accessor
(_split_lhs_rhs, _wrap_rhs), and the domain constants (_TERMINAL,
_LOOP_KINDS) every sweep reads through self."""
from __future__ import annotations

import os
import re
import sys
import time

from struct_node import (Node, compound_match, _RMULTI_LHS_RE,
                         _RREF_RE, _RFOR_BIND_RE)


class SweepUtilMixin:
    """Shared traversal and text policy: the single owners of the occurrence query (_occurs), the quote/comment-aware text scanners (_quoted_runs, _code_part, _unquoted, _split_top), the assignment accessor (_split_lhs_rhs, _wrap_rhs), and the domain constants (_TERMINAL, _LOOP_KINDS) every sweep reads through self."""

    def _pass_time(self, name: str, t0: float) -> None:
        # Debug backdoor mirroring the reference's REAL_TIME/REAL_DUMP env
        # flags: OUR_TIME=1 prints per-pass timings to stderr, no behavior
        # change otherwise.
        if os.environ.get("OUR_TIME"):
            dt = (time.perf_counter() - t0) * 1000.0
            print(f"  {name:26} {dt:6.1f} ms", file=sys.stderr)
    _TERMINAL = ("return", "goto", "break", "continue")
    _LOOP_KINDS = ("while", "repeat", "numfor", "genfor")
    def _has_goto_or_label(self, nodes: list[Node]) -> bool:
        return self._occurs(nodes, -1, roles=("flow",))
    @staticmethod
    def _quoted_runs(s: str) -> list[tuple[str, bool]]:
        """Split into (segment, is_quoted) runs; single/double/backtick
        quotes with escapes count as quoted."""
        runs, cur, inq, i = [], [], None, 0
        while i < len(s):
            ch = s[i]
            if inq:
                cur.append(ch)
                if ch == "\\" and i + 1 < len(s):
                    cur.append(s[i + 1])
                    i += 2
                    continue
                if ch == inq:
                    runs.append(("".join(cur), True))
                    cur, inq = [], None
            elif ch in "'\"`":
                if cur:
                    runs.append(("".join(cur), False))
                    cur = []
                inq, cur = ch, [ch]
            else:
                cur.append(ch)
            i += 1
        if cur:
            runs.append(("".join(cur), inq is not None))
        return runs
    def _code_part(self, text: str) -> tuple[str, str]:
        """Split a stmt line into (code, comment) at the first ` -- `
        outside quotes; backtick regions count as quoted too."""
        # scan unquoted segments with offsets for the comment split
        pos = 0
        for seg, quoted in self._quoted_runs(text):
            if not quoted:
                j = seg.find(" -- ")
                if j >= 0:
                    return text[:pos + j], text[pos + j + 1:]
            pos += len(seg)
        return text, ""
    @staticmethod
    def _unquoted(s: str) -> str:
        """`s` with single/double/backtick-quoted regions (and escapes)
        blanked, so text scans for code tokens skip string contents."""
        out, inq, i = [], None, 0
        while i < len(s):
            ch = s[i]
            if inq:
                out.append(" ")
                if ch == "\\" and i + 1 < len(s):
                    out.append(" ")
                    i += 2
                    continue
                if ch == inq:
                    inq = None
            elif ch in "'\"`":
                inq = ch
                out.append(" ")
            else:
                out.append(ch)
            i += 1
        return "".join(out)
    def _split_top(self, s: str, sep: str) -> list[str]:
        """Split on `sep` at paren/bracket/brace depth 0, skipping quoted
        regions; a `sep` char touching an identical third char (vararg
        `...`, `..top`) never splits."""
        parts, depth, i, last = [], 0, 0, 0
        inq = None
        while i < len(s):
            ch = s[i]
            if inq:
                if ch == "\\" and i + 1 < len(s):
                    i += 2
                    continue
                if ch == inq:
                    inq = None
            elif ch in "'\"`":
                inq = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            elif depth == 0 and s.startswith(sep, i):
                before = s[i - 1] if i > 0 else ""
                after = s[i + len(sep)] if i + len(sep) < len(s) else ""
                if before != sep[0] and before != "." and after != sep[-1] and after != ".":
                    parts.append(s[last:i])
                    i += len(sep)
                    last = i
                    continue
            i += 1
        parts.append(s[last:])
        return parts
    _ATOMIC_RHS = re.compile(
        r"^(R\d+|U\d+|-?\d+(?:\.\d+)?|true|false|nil|" + r"'[^']*'|\"[^\"]*\"|" + r"\{.*\}|" + r"[A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)*|" + r"R\d+(?:\.\w+|\[[^\]]+\])*)$")
    _FIELD_LHS_RE = re.compile(r"^(R\d+(?:\.\w+|\[[^\]]+\])*) = ")
    def _occurs(self, nodes: list[Node], reg: int, skip_ids=frozenset(),
                roles: tuple = ("rhs", "cond", "header")) -> bool:
        """Single recursive occurrence query over a node list (the one
        subtree walker behind the inline/copy-prop guards, the loop-tail
        cleaner and the label check). True when register R<reg> occurs
        in any of the given roles. Roles: `lhs` (killed by a plain or
        compound `=` LHS, via _lhs_defs), `rhs` (read as a value in a
        stmt, LHS excluded), `cond` (any condition text), `header` (any
        loop-header text), `hbind` (register bound by a `for` header),
        `compound` (writer of an `R<reg> <op>=` line, a def+read the
        copy propagator must never touch), `flow` (`goto`/`label` nodes,
        reg ignored), `ret` (value reads in `return` texts). Nodes
        whose id is in `skip_ids` are ignored but still descended into.
        Only `stmt` texts count as lhs/rhs -- `return`-kind value
        reads stay invisible unless `ret` is requested, exactly as the
        old per-pass walkers behaved."""
        for nd in nodes:
            if id(nd) not in skip_ids:
                if "compound" in roles and nd.kind == "stmt" and nd.text:
                    mc = compound_match(nd.text)
                    if mc is not None and int(mc.group(1)) == reg:
                        return True
                if ("lhs" in roles or "rhs" in roles) and nd.kind == "stmt" and nd.text:
                    if "lhs" in roles and reg in self._lhs_defs(nd.text):
                        return True
                    if "rhs" in roles and reg in self._value_refs(nd.text):
                        return True
                if "ret" in roles and nd.kind == "return" and nd.text:
                    if reg in self._value_refs(nd.text):
                        return True
                if "flow" in roles and nd.kind in ("goto", "label"):
                    return True
                if "cond" in roles and nd.cond is not None:
                    try:
                        if reg in {int(x) for x in _RREF_RE.findall(nd.cond.render())}:
                            return True
                    except Exception:
                        pass
                if nd.header and ("header" in roles or "hbind" in roles):
                    if "header" in roles and reg in {int(x) for x in _RREF_RE.findall(nd.header)}:
                        return True
                    if "hbind" in roles:
                        m = _RFOR_BIND_RE.match(nd.header)
                        if m and reg in {int(x[1:]) for x in m.group(1).split(", ")}:
                            return True
            if nd.then_ and self._occurs(nd.then_, reg, skip_ids, roles):
                return True
            if nd.else_ and self._occurs(nd.else_, reg, skip_ids, roles):
                return True
        return False
    def _wrap_rhs(self, rhs: str) -> str:
        if self._ATOMIC_RHS.match(rhs.strip()):
            return rhs.strip()
        return f"({rhs.strip()})"
    def _split_lhs_rhs(self, text: str) -> tuple[str, str] | None:
        """Split a `R... = ...` stmt into (lhs, rhs) incl. compound; None
        when the line is not a plain/compound register assignment."""
        m = _RMULTI_LHS_RE.match(text)
        if m:
            return m.group(1), text[m.end():]
        m = compound_match(text)
        if m:
            # compound lhs is also a read; return the operator tail as rhs
            # with a marker so callers never treat it as a plain def.
            return f"R{m.group(1)}", text[m.end():]
        return None
