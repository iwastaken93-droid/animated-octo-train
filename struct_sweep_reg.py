"""Register-alias sweeps: compound-assign folding, single-use temp inlining, and copy propagation, with their confinement queries."""
from __future__ import annotations

import re

from struct_node import (Node, compound_match, _RMULTI_LHS_RE,
                         _RREF_RE, _RFOR_BIND_RE)


class RegMixin:
    """Register-alias sweeps: compound-assign folding, single-use temp inlining, and copy propagation, with their confinement queries."""

    _LOOPVAR_POOL = ("i", "j", "k")
    def _flat_code(self, nodes: list[Node]) -> str:
        """One subtree's code with comments and string contents blanked:
        the single text surface for after-range reg scans and the
        multret/`..top` check (string contents can never be reg refs)."""
        out: list[str] = []
        def bare(text: str) -> str:
            code, _ = self._code_part(text)
            return "".join(seg if not q else "" for seg, q in self._quoted_runs(code))
        def walk(ns: list[Node]) -> None:
            for nd in ns:
                if nd.text:
                    out.append(bare(nd.text))
                if nd.header:
                    out.append(bare(nd.header))
                if nd.cond is not None:
                    try:
                        out.append(bare(nd.cond.render()))
                    except Exception:
                        pass
                if nd.then_:
                    walk(nd.then_)
                if nd.else_:
                    walk(nd.else_)
        walk(nodes)
        return "\n".join(out)
    def _after_status(self, lst: list[Node], idx: int, reg: int,
                      tails: tuple = ()) -> str:
        """The after-range of the loop at lst[idx] for R<reg>: "clean"
        (no read can observe the loop no longer writing it), "reads"
        (some undominated read can), or "multret" (a `..top`/multret
        slot is involved, which keys on the register number itself).
        Scans same-list siblings then enclosing ranges in execution
        order with kill-awareness carried across levels: an
        unconditional plain re-def dominates everything after it,
        while branch interiors count reads but never kill."""
        pat = rf"\bR{reg}\b\.\.top|multret"
        seen_read = False
        killed = False
        for seq in [lst[idx + 1:]] + [list(t) for t in tails]:
            for nd in seq:
                if killed and not seen_read:
                    # A dominating re-def already makes later readers
                    # (and later multret slots) observe the kill, not the
                    # loop -- nothing after this point can block.
                    return "clean"
                if nd.kind in ("stmt", "return") and nd.text:
                    code, _ = self._code_part(nd.text)
                    if re.search(pat, code):
                        return "multret"
                    if reg in self._value_refs(nd.text):
                        seen_read = True
                    elif (nd.kind == "stmt"
                          and reg in self._lhs_defs(nd.text)
                          and not compound_match(code.strip())):
                        killed = True
                elif nd.then_ or nd.else_ or nd.header or nd.cond is not None:
                    if re.search(pat, self._flat_code([nd])):
                        return "multret"
                    if self._occurs([nd], reg,
                                     roles=("rhs", "cond", "header", "ret")):
                        seen_read = True
        return "reads" if seen_read else "clean"
    def _rename_scoped(self, nodes: list[Node], reg: int, name: str) -> None:
        """Rename R<reg> -> name over a loop body, stopping at nested
        headers that rebind it (a different variable: only their header
        *rest* still reads ours, so just that is rewritten and their
        bodies are left for their own firing)."""
        for nd in nodes:
            if nd.header:
                m = _RFOR_BIND_RE.match(nd.header)
                if m and reg in {int(x[1:]) for x in m.group(1).split(", ")}:
                    rest = nd.header[m.end():]
                    nd.header = nd.header[:m.end()] + self._sub_reg(rest, reg, name)
                    continue
                nd.header = self._sub_reg(nd.header, reg, name)
            if nd.kind in ("stmt", "return") and nd.text:
                code, _ = self._code_part(nd.text)
                if "..top" in code or "multret" in code:
                    continue  # multret slots key on the register number
                nd.text = self._sub_reg(nd.text, reg, name)
            if nd.cond is not None:
                nd.cond.l = self._sub_reg(nd.cond.l, reg, name)
                nd.cond.r = self._sub_reg(nd.cond.r, reg, name)
            if nd.then_:
                self._rename_scoped(nd.then_, reg, name)
            if nd.else_:
                self._rename_scoped(nd.else_, reg, name)
    def _split_self_init_loop(self, lst: list[Node],
                              tails: tuple = ()) -> int:
        """Split `for R8 = R8, ...` (20 corpus sites): the init reads an
        *outer* R8 while the header binds a fresh loop-local, so the two
        variables sharing one register textually conflate them. Give the
        loop-local a fresh `i`/`j`/`k` name over its dominated range
        (bind + body, shadowing-aware). Fires when no after-read can
        observe the loop no longer writing R8 (kill-aware scan over
        same-list siblings then enclosing ranges in execution order),
        or -- when only after-reads stand in the way -- with a
        spill/restore pair (`i = R8` before for zero-trip exactness,
        `R8 = i` after) that makes the rename invisible. The body and
        the whole after-range must be multret/`..top`-free (slots key
        on the register number). Post-fixpoint: renamed lines are
        inert to the R#-keyed gates by construction."""
        n = 0
        k = 0
        while k < len(lst):
            nd = lst[k]
            advance = 1
            if nd.header:
                m = _RFOR_BIND_RE.match(nd.header)
                if m and m.group(2) == "=" and nd.then_:
                    bound = [int(x) for x in _RREF_RE.findall(m.group(1))]
                    rest = nd.header[m.end():]
                    top = self._split_top(rest, ",")
                    init = top[0] if top else ""
                    flat = self._flat_code(nd.then_)
                    for b in bound:
                        bind = m.group(1)
                        if not re.search(rf"\bR{b}\b", init):
                            continue
                        if re.search(rf"\bR{b}\b\.\.top|multret", flat):
                            continue  # multret slots key on the register number
                        # One after-range query decides the firing mode:
                        # clean renames in place, reads rename behind a
                        # spill/restore pair (`i = R8` up front keeps
                        # zero-trip exact, `R8 = i` after dominates every
                        # later reader), multret refuses (slots key on the
                        # register number itself).
                        status = self._after_status(lst, k, b, tails)
                        if status == "multret":
                            continue
                        spill = (status == "reads")
                        taken = self._taken_names(self._nodes)
                        name = next((c for c in list(self._LOOPVAR_POOL)
                                     + [f"i{i}" for i in range(2, 99)]
                                     if c not in taken), None)
                        if name is None:
                            continue
                        nd.header = (nd.header[:m.start(1)]
                                     + re.sub(rf"\bR{b}\b", name, bind)
                                     + nd.header[m.end(1):])
                        self._rename_scoped(nd.then_, b, name)
                        if spill:
                            lst.insert(k, Node("stmt", f"{name} = R{b}"))
                            lst.insert(k + 2, Node("stmt", f"R{b} = {name}"))
                            advance = 3  # pre, loop (recursed below), post
                        n += 1
            if nd.then_ or nd.else_:
                tails2 = ((lst[k + 1:],) + tails)
                if nd.then_:
                    n += self._split_self_init_loop(nd.then_, tails2)
                if nd.else_:
                    n += self._split_self_init_loop(nd.else_, tails2)
            k += advance
        return n
    _COMPOUND_SRC = re.compile(r"^R(\d+) = R\1\s*(\.\.|//|[+\-*/%^])\s*(.+)$")
    _COPY_SRC = re.compile(r"^R(\d+) = R(\d+)$")
    def _tmp_read_elsewhere(self, tmp: int, defnode: Node, usenode: Node) -> bool:
        """True when Rtmp is READ anywhere else in the whole function tree
        besides the single use stmt -- another value-read, or any
        cond/header occurrence (positions the inliner never rewrites).
        Other DEFS are harmless and ignored: same-list straight-line
        order means our def kills earlier ones for our use, and later
        ones only kill ours after our use. (A second def strictly
        between def and use is still refused by the caller's
        between-check.)"""
        return self._occurs(self._nodes, tmp,
                            skip_ids=frozenset({id(defnode), id(usenode)}),
                            roles=("rhs", "cond", "header"))
    def _dst_read_outside(self, dst: int, copyline: Node, allowed_ids: set[int]) -> bool:
        """True when Rdst is read anywhere in the function tree outside
        the copy line and the collected rewrite set: another stmt RHS,
        any cond/header, any `return` (which the rewriter below leaves
        alone -- a `return Rdst` kept reading a deleted alias), or any
        compound `Rdst <op>=` line (a def+read the propagator must never
        touch). Other plain DEFS are harmless (kills, ordered by the
        caller's later-subtree check). Without this, ancestor-scope
        readers (e.g. a use after the enclosing branch) would keep
        reading the deleted alias."""
        return self._occurs(self._nodes, dst,
                            skip_ids=frozenset({id(copyline)}) | set(allowed_ids),
                            roles=("rhs", "cond", "header", "ret", "compound"))
    _DEF_ROLES = ("lhs", "hbind")
    def _compound_assigns(self, lst: list[Node]) -> int:
        """Fold `R0 = R0 <op> K` into `R0 <op>= K` (Tovek compound_bases,
        restricted to plain-register bases where single evaluation is
        exact; table bases stay expanded since `t[k]` would evaluate
        twice). Luau parses all eight (`+= -= *= /= //= %= ^= ..=`).
        Preserves trailing `-- comment` tails. Never pure (reads R0)."""
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._compound_assigns(nd.then_)
            if nd.else_:
                n += self._compound_assigns(nd.else_)
        _MAP = {"+": "+=", "-": "-=", "*": "*=", "/": "/=", "//": "//=",
                "%": "%=", "^": "^=", "..": "..="}
        for nd in lst:
            if nd.kind != "stmt" or not nd.text:
                continue
            text, comment = nd.text, ""
            if " -- " in text:
                text, comment = text.split(" -- ", 1)
                comment = " -- " + comment
            if "..top" in text or "multret" in text:
                continue
            m = self._COMPOUND_SRC.match(text.strip())
            if not m:
                continue
            reg, op, rest = m.group(1), m.group(2), m.group(3).strip()
            if not rest or compound_match(text.strip()):
                continue
            nd.text = f"R{reg} {_MAP[op]} {rest}{comment}"
            nd.pure, nd.defreg = False, int(reg)
            n += 1
        return n
    def _inline_single_use_temps(self, lst: list[Node]) -> int:
        """Inline a pure single-use temp back into its use (Tovek
        inline_single_use_temps, same-list stmt-only restriction):
        `Rtmp = <pure, call-free>; ... Rdst = ...Rtmp...` becomes
        `Rdst = ...(<pure>)...` and the def is dropped. Gate: exactly one
        read in this list, no redefinition of Rtmp or of any dep between
        def and use, initializer has no calls/parens (evaluation order
        cannot shift). Parenthesizes non-atomic inits so precedence is
        exact (`R1=a+b; R2=R1*2` -> `R2=(a+b)*2`)."""
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._inline_single_use_temps(nd.then_)
            if nd.else_:
                n += self._inline_single_use_temps(nd.else_)
        i = 0
        while i < len(lst):
            nd = lst[i]
            if (nd.kind != "stmt" or not nd.pure or nd.defreg is None
                    or not nd.text):
                i += 1
                continue
            parts = self._split_lhs_rhs(nd.text)
            if parts is None:
                i += 1
                continue
            lhs, rhs = parts
            if "," in lhs or "(" in rhs or "..top" in rhs or "multret" in rhs:
                i += 1
                continue
            tmp = nd.defreg
            if not re.fullmatch(rf"R{tmp}", lhs.strip()):
                i += 1
                continue
            # strip the def's own `-- comment` (lifter provenance like
            # `-- dup K33`): it must not travel inside parens; re-append
            # to the use line when the use has none.
            rhs_code, _, rhs_cmt = rhs.partition(" -- ")
            rhs = rhs_code.strip()
            def_cmt = rhs_cmt.strip()
            if not rhs:
                i += 1
                continue
            if tmp in {int(x) for x in _RREF_RE.findall(rhs)}:
                i += 1
                continue  # self-referential init: inlining would dangle
            # single read in this list, in a later stmt's value position
            uses: list[int] = []
            total_reads = 0
            for j in range(i + 1, len(lst)):
                uj = lst[j]
                if uj.kind != "stmt" or not uj.text:
                    continue
                refs = self._value_refs(uj.text)
                c = sum(1 for _ in re.finditer(rf"\bR{tmp}\b", uj.text.split(" -- ", 1)[0]))
                # value_refs excludes LHS; count only real value reads
                if tmp in refs:
                    uses.append(j)
                    total_reads += c
            if total_reads != 1 or len(uses) != 1:
                i += 1
                continue
            j = uses[0]
            uj = lst[j]
            # no redefinition of tmp or of any dep between def and use
            # (same-list stmts, loop-header binds, and nested writes);
            # plus no occurrence of tmp anywhere else in the function
            # (another def/read, or a cond/header reader the rewriter
            # leaves alone -- see the R7/R5 miscompile that dropped a
            # `if R7` guard's reaching def).
            deps = {int(x) for x in _RREF_RE.findall(rhs)}
            blocked = False
            for k in range(i + 1, j):
                kk = lst[k]
                if kk.kind == "stmt" and kk.text and (
                        tmp in self._lhs_defs(kk.text) or deps & set(self._lhs_defs(kk.text))):
                    blocked = True
                    break
                if kk.header:
                    m = _RFOR_BIND_RE.match(kk.header)
                    if m:
                        bound = {int(x[1:]) for x in m.group(1).split(", ")}
                        if tmp in bound or deps & bound:
                            blocked = True
                            break
                if kk.cond is not None or kk.then_ or kk.else_:
                    if (self._occurs([kk], tmp, roles=self._DEF_ROLES)
                            or any(self._occurs([kk], d, roles=self._DEF_ROLES) for d in deps)):
                        blocked = True
                        break
            if not blocked:
                blocked = self._tmp_read_elsewhere(tmp, nd, uj)
            if blocked:
                i += 1
                continue
            # substitute into the use's RHS only (never its LHS).
            # A hole that IS the whole RHS needs no parens
            # (`R0.y = R1` -> `R0.y = R0.x + 1`); an embedded hole
            # gets parens so precedence stays exact.
            uj = lst[j]
            m = (_RMULTI_LHS_RE.match(uj.text) or self._FIELD_LHS_RE.match(uj.text))
            if m:
                lhs_j, rhs_j = uj.text[:m.end()], uj.text[m.end():]
                code, sep, cmt = rhs_j.partition(" -- ")
                repl = rhs if code.strip() == f"R{tmp}" else self._wrap_rhs(rhs)
                code = re.sub(rf"\bR{tmp}\b", repl, code)
                if def_cmt and not sep:
                    sep, cmt = " -- ", def_cmt
                uj.text = lhs_j + code + (" -- " + cmt if sep else "")
            else:
                repl = rhs if uj.text.strip() == f"R{tmp}" else self._wrap_rhs(rhs)
                uj.text = re.sub(rf"\bR{tmp}\b", repl, uj.text)
                if def_cmt and " -- " not in uj.text:
                    uj.text += " -- " + def_cmt
            del lst[i]
            n += 1
            # stay at i: the shifted element needs scanning
            continue
        return n
    def _propagate_copies(self, lst: list[Node]) -> int:
        """Drop `Rdst = Rsrc` aliases, rewriting later value-uses to Rsrc
        (Tovek copy_cleanup, register-adapted): sound iff neither Rsrc nor
        Rdst is redefined afterwards in this subtree and Rdst never occurs
        in a later cond/header/return/compound-LHS (positions the rewriter
        leaves alone). Only rewrites later stmt RHS text. A copy with no
        later use at all is dropped when the straight-line tail to the
        list's unconditional `return` reads Rdst in no role (a later Rsrc
        redefinition is then irrelevant -- nothing is rewritten)."""
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._propagate_copies(nd.then_)
            if nd.else_:
                n += self._propagate_copies(nd.else_)
        i = 0
        while i < len(lst):
            nd = lst[i]
            if nd.kind != "stmt" or not nd.text:
                i += 1
                continue
            code = nd.text.split(" -- ", 1)[0].strip()
            m = self._COPY_SRC.match(code)
            if not m or "..top" in nd.text or "multret" in nd.text:
                i += 1
                continue
            dst, src = int(m.group(1)), int(m.group(2))
            if dst == src:
                del lst[i]
                n += 1
                continue
            later = lst[i + 1:]
            # Whole-function confinement: every READ of Rdst must be one
            # of the later stmt RHSs rewritten below (compound `Rdst`
            # lines never qualify). Catches cond/header/return readers
            # and ancestor-scope or nested-branch readers the rewriter
            # leaves alone -- they would read the deleted alias.
            rewrite_ids = {id(nd2) for nd2 in later
                           if nd2.kind == "stmt" and nd2.text
                           and not compound_match(nd2.text)
                           and dst in self._value_refs(nd2.text)}
            if not rewrite_ids:
                # Dead copy: no later use in this list to propagate to
                # (a later Rsrc redefinition is irrelevant -- nothing
                # is rewritten to Rsrc). Drop it only when nothing past
                # it can observe the value: the tail of this list is
                # straight-line code running to an unconditional
                # `return` (so enclosing tails and loop back-edges are
                # unreachable past it), with no Rdst read in any role
                # and no `..top`/multret slot keyed on the register.
                rest = lst[i + 1:]
                if (rest and rest[-1].kind == "return"
                        and all(nd.kind in ("stmt", "return") for nd in rest)
                        and not self._occurs(rest, dst, roles=(
                            "rhs", "cond", "header", "ret", "compound"))
                        and not re.search(rf"\bR{dst}\b\.\.top|multret",
                                          self._flat_code(rest))):
                    del lst[i]
                    n += 1
                    continue
                i += 1
                continue
            if (self._occurs(later, dst, roles=self._DEF_ROLES)
                    or self._occurs(later, src, roles=self._DEF_ROLES)):
                i += 1
                continue
            if self._dst_read_outside(dst, nd, rewrite_ids):
                i += 1
                continue
            pat = re.compile(rf"\bR{dst}\b")
            for nd2 in later:
                if nd2.kind != "stmt" or not nd2.text:
                    continue
                mm = _RMULTI_LHS_RE.match(nd2.text)
                if mm:
                    lhs_j, rhs_j = nd2.text[:mm.end()], nd2.text[mm.end():]
                    code_j, sep, cmt = rhs_j.partition(" -- ")
                    code_j = pat.sub(f"R{src}", code_j)
                    nd2.text = lhs_j + code_j + (" -- " + cmt if sep else "")
                else:
                    nd2.text = pat.sub(f"R{src}", nd2.text)
            del lst[i]
            n += 1
            continue
        return n
