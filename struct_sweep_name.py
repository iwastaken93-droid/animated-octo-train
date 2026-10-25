"""Usage-evidence naming: single-def register rename plus spelling owner.

Renames only registers with exactly one plain `R# = ...` definition in the
whole proto (no compound/`..top` writers, no `for` binds), so every textual
mention provably belongs to one live range and a textual rewrite is exact.
Evidence is read off the definition RHS only (Tovek name_locals, adapted to
our flat R# text): `:Connect` (with `Touched`-style event and
`GetPropertyChangedSignal` specializations), `:Clone`, `:GetAttribute`,
`require`, `.new("Name")` factories, single-arg coercion transparency
(`tostring(R0.v)` -> `v`, else the kind fallback `str`/`kind`), the
method-call tier (getters, participles, transparent case folds), dotted
static-path calls (`math.abs` -> `abs`, `table.concat` -> `joined`),
table constructors (kind-certain `{...}` -> `tbl`), and upvalue loads
(`R = U0` named after the proto's debug upvalue table). Runs once
after the fixpoint in `structure()`, on by default: sweep gates never see
renamed text, and `names=False` keeps byte-identical R-numbered output.

The spelling owner (`_sanitize`, `_binding_spelling`, `_suffixed`) ports
Tovek name_spelling's policy: lowerCamel, no keywords/`self`, soft-reserved
builtins take their conventional alternative, counters never run into a
name's own digits.
"""
from __future__ import annotations

import re

from struct_node import Node, compound_match, _RFOR_BIND_RE, _RMULTI_LHS_RE


class NameMixin:
    """Usage-evidence naming (mixed into Structurer; runs post-fixpoint)."""

    # -- spelling owner --
    _LUAU_KEYWORDS = frozenset({
        "local", "function", "end", "if", "then", "else", "elseif",
        "for", "while", "do", "return", "break", "continue", "repeat",
        "until", "in", "and", "or", "not", "nil", "true", "false",
    })
    _SOFT_RESERVED = (
        "CFrame", "Color3", "Enum", "Instance", "UDim", "UDim2",
        "Vector2", "Vector3", "_ENV", "_G", "_VERSION", "assert",
        "bit32", "buffer", "coroutine", "debug", "error", "game",
        "getfenv", "getmetatable", "ipairs", "math", "newproxy",
        "next", "os", "pairs", "pcall", "plugin", "print", "rawequal",
        "rawget", "rawlen", "rawset", "require", "script", "select",
        "setfenv", "setmetatable", "shared", "string", "table",
        "tonumber", "tostring", "type", "typeof", "unpack", "utf8",
        "warn", "workspace", "xpcall",
    )
    _BUILTIN_ALTERNATIVE = {
        "error": "err", "type": "kind", "next": "nextNode",
        "string": "str", "table": "tbl", "select": "selection",
        "buffer": "buf",
    }

    @staticmethod
    def _lower_camel(name: str) -> str:
        """Lowercase the leading word: `Part` -> `part`, `ID` -> `id`."""
        if not name:
            return name
        if name.isupper():
            return name.lower()
        return name[0].lower() + name[1:]

    @classmethod
    def _sanitize(cls, raw: str) -> str | None:
        """Raw evidence token -> lowerCamel identifier, or None when the
        token carries no usable word (empty, keyword, `self`, `_`)."""
        name = "".join(c for c in raw if c.isascii() and (c.isalnum() or c == "_"))
        if not name:
            return None
        if name.startswith("_") and len(name) >= 3 and name[1].isalpha():
            name = name[1:]  # private-field `_` says nothing about the binding
        name = cls._lower_camel(name)
        if (not name or name[0].isdigit() or name == "_" or name == "self"
                or name in cls._LUAU_KEYWORDS):
            return None
        if re.fullmatch(r"[ru]\d+", name):
            return None  # register doppelganger (`Get('R1')` -> `r1`):
        return name

    @staticmethod
    def _sanitize_preserve(raw: str) -> str | None:
        """Module-name spelling: sanitized but case-preserved, so
        `require(script.Signal)` binds `Signal` the way source does."""
        name = "".join(c for c in raw if c.isascii() and (c.isalnum() or c == "_"))
        if not name:
            return None
        if name[0].isdigit() or name == "_" or name == "self":
            return None
        if name in NameMixin._LUAU_KEYWORDS:
            return None
        return name

    @classmethod
    def _binding_spelling(cls, name: str) -> str:
        """The spelling a binding finally takes: soft-reserved builtins
        read their conventional alternative (`type` -> `kind`), a
        still-reserved result counts past the builtin (`Enum` -> `Enum2`)."""
        if name not in cls._SOFT_RESERVED:
            return name
        alt = cls._BUILTIN_ALTERNATIVE.get(name)
        if alt is None:
            alt = name[0].upper() + name[1:] if name else name
        if alt not in cls._SOFT_RESERVED:
            return alt
        return cls._suffixed(name, 2)

    @staticmethod
    def _suffixed(base: str, counter: int) -> str:
        """`part2`, but `bit32_2`: the counter never reads as part of a
        name that already ends in a digit."""
        return f"{base}_{counter}" if base[-1].isdigit() else f"{base}{counter}"

    def _free_spelling(self, base: str, taken: set[str],
                       claimed: set[str]) -> str:
        """First free spelling of an already-spelled base: the bare
        name when available, else the first free numeric suffix
        (`start2`). Single owner of the suffix policy shared by the
        loop-operand, triple, and loop-variable paths (evidence names
        never suffix: they refuse on collision instead)."""
        name, counter = base, 2
        while name in taken or name in claimed:
            name = self._suffixed(base, counter)
            counter += 1
        return name

    # -- evidence shapes (matched against the definition's code part) --
    # An upvalue load carries its debug name two ways: the lifter renders
    # `U0(seed)` inline when the proto has debug info, otherwise bare
    # `U0` and the name comes from the proto's upvalue table (plumbed
    # through `upvals`). Either way the local is a copy of that upvalue,
    # so naming it after the upvalue mirrors source (`local x = seed`).
    _UPVAL_RE = re.compile(r"^U(\d+)(?:\(([^)]*)\))?$")
    # The `(name)` annotation is metadata, not a read: the taken-names
    # probe strips it so the annotation itself never blocks its own name.
    _UPVAL_ANNOT_RE = re.compile(r"\bU\d+\(([^)]*)\)")
    _CONNECT_RE = re.compile(r"^(?P<recv>.+):Connect\s*\(")
    _CLONE_RE = re.compile(r"^(?P<recv>.+):Clone\s*\(")
    _GETATTR_RE = re.compile(
        r"^.+:GetAttribute\s*\(\s*['\"](?P<key>[^'\"]+)['\"]\s*\)$")
    _REQUIRE_RE = re.compile(r"^require\s*\(\s*(?P<arg>.+?)\s*\)$")
    _NEW_RE = re.compile(
        r"^(?P<recv>[A-Za-z_][\w.]*)\.new\s*\(\s*['\"](?P<arg>[A-Za-z_]\w*)['\"]")
    _CLOSURE_RE = re.compile(r"^dupclosure\s+'(?P<name>[^']+)'$")
    _STRARG_RE = re.compile(
        r"^.+:[A-Za-z_]\w*\(\s*['\"](?P<lit>[^'\"]+)['\"]\s*\)$")
    # Pure builtins whose return kind is fixed: `tostring` always makes
    # a string, `type` always makes a type-name string. Bare callee
    # only (dotted `string.format` is a different operation with its own
    # identity); form-dependent (`select`) and arg-returning (`assert`)
    # builtins refuse. Evidence feeds the spelling owner, so `type`
    # reads its conventional `kind` alternative for free.
    _PURE_KIND_RE = re.compile(r"^(?P<callee>tostring|type)\s*\(")
    # A table constructor's kind is certain (`{}` is always a table):
    # name it after the kind, like the coercion fallbacks above. The
    # spelling owner maps `table` to its conventional `tbl` alternative,
    # and the taken/claimed gates refuse when `tbl` is already spelled.
    _TABLE_CTOR_RE = re.compile(r"^\{")
    # A dotted field tail (`R0.v` -> `v`): the transparent-arg fallback
    # for coercions below. Kept inside the coercion path (not a general
    # rule) so bare field-copy single-defs stay R-numbered as today.
    _FIELD_TAIL_RE = re.compile(r"^.+\.([A-Za-z_]\w*)$")
    # General method-call tier (Tovek method_call_hint, unambiguous core):
    # transparent case folds name after their receiver, bare action verbs
    # refuse (their result is not "an add"), Get-prefix getters strip to
    # their noun, and transform verbs read their result participle. Only
    # reached when no specific rule above claimed the RHS.
    _METHOD_RE = re.compile(
        r"^(?P<recv>.+):(?P<meth>[A-Za-z_]\w*)\((?P<args>.*)\)$")
    # Dotted static-path call (`math.abs(-5)` -> `abs`): the library path
    # plus operation name unambiguously describes the result, the same
    # reason factories (`Instance.new`) read a static path. Checked after
    # the specific rules so `X.new('Lit')` keeps its factory name.
    _DOTTED_CALL_RE = re.compile(
        r"^(?P<recv>[A-Za-z_][\w.]*)\.(?P<fn>[A-Za-z_]\w*)\((?P<args>.*)\)$")
    _ACTION_VERBS = frozenset({
        "invoke", "fire", "destroy", "set", "add", "remove", "update",
        "apply", "play", "stop", "pause", "resume", "wait", "connect",
        "disconnect", "clear", "reset", "save", "send", "emit",
        "register", "unregister", "bind", "unbind", "call", "run",
        "start", "init", "initialize", "push", "pop", "insert",
        "append", "prepend", "attach", "detach", "move", "show",
        "hide", "toggle", "enable", "disable", "cancel", "kick",
        "ban", "teleport", "print", "warn", "log", "sub", "gsub",
        "gmatch", "find", "rep", "byte", "len", "get", "put",
        "delete", "dispose", "cleanup", "clean", "close", "open",
        "notify", "dispatch", "trigger", "listen", "subscribe",
        "unsubscribe", "mount", "unmount", "refresh", "reload",
        "flush", "tick", "step", "advance", "execute", "exec",
        "perform", "do", "process", "handle", "on", "once",
        "await", "yield", "suspend", "throw", "raise", "error",
        "assert", "check", "verify", "test", "ensure", "require",
        "use", "try", "catch", "finally", "then", "defer",
        "delay", "schedule", "queue", "enqueue", "dequeue",
        "publish", "broadcast", "replicate", "sync", "animate",
        "change", "modify", "mutate", "configure", "setup",
        "install", "uninstall", "write", "read", "seek", "scan",
        "walk", "visit", "iterate", "loop", "repeat", "retry",
        "abort", "exit", "quit", "kill", "spawn", "respawn",
        "restore", "backup", "store", "focus", "blur", "select",
        "deselect", "highlight", "draw", "paint", "erase",
        "validate", "sanitize", "bump", "increment", "decrement",
        "accumulate", "watch", "observe", "poll", "ping", "post",
        "patch", "make", "create", "build", "new", "destruct",
        "construct", "release", "acquire", "lock", "unlock",
        "grant", "revoke", "give", "take", "drop", "pick",
        "equip", "unequip", "cast", "reject", "fulfill", "finish",
        "complete", "begin", "end", "peek", "seed", "activate",
        "deactivate", "trace", "debug", "prepare", "compose",
        "invalidate", "mark", "unmark", "tag", "untag", "swap",
        "exchange", "replace", "assign", "reconcile", "render",
        "consume", "produce", "collect", "gather", "dump",
    })
    _VERB_PARTICIPLE = {
        "merge": "merged", "clone": "clone", "copy": "copy",
        "sort": "sorted", "filter": "filtered", "map": "mapped",
        "parse": "parsed", "format": "formatted", "split": "parts",
        "join": "joined", "concat": "joined", "trim": "trimmed",
        "round": "rounded", "clamp": "clamped", "freeze": "frozen",
        "reverse": "reversed", "shuffle": "shuffled",
        "flatten": "flattened", "encode": "encoded",
        "decode": "decoded", "normalize": "normalized",
        "resolve": "resolved", "compute": "computed",
        "calculate": "calculated", "sum": "sum", "count": "count",
        "convert": "converted", "scale": "scaled",
        "transform": "transformed", "escape": "escaped",
        "combine": "combined", "reduce": "reduced",
        "extend": "extended", "tween": "tween",
    }
    _WORD_RE = re.compile(r"^[A-Za-z_]\w*$")
    _DOTTED_RE = re.compile(r"^[A-Za-z_][\w.]*$")
    _SIGNAL_ARG_RE = re.compile(
        r":Get(?:Property|Attribute)ChangedSignal\s*\(\s*['\"](?P<key>[^'\"]+)['\"]\s*\)$")

    @classmethod
    def _evidence_name(cls, rhs: str, upvals: tuple = ()) -> str | None:
        """Candidate name from a definition RHS, or None when no rule
        reads it. `upvals` is the proto's debug upvalue table (empty when
        stripped); callers apply `_binding_spelling` and collision checks."""
        m = cls._UPVAL_RE.match(rhs)
        if m:
            idx = int(m.group(1))
            inline = m.group(2)
            raw = inline if inline else (upvals[idx] if 0 <= idx < len(upvals) else None)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            return cls._sanitize(str(raw))
        m = cls._CONNECT_RE.match(rhs)
        if m:
            recv = m.group("recv")
            ms = cls._SIGNAL_ARG_RE.search(recv)
            if ms:
                key = cls._sanitize(ms.group("key") + "Changed")
                base = (key + "Connection") if key else "connection"
            elif "." in recv and cls._DOTTED_RE.match(recv):
                event = cls._sanitize(recv.rsplit(".", 1)[1])
                base = event + "Connection" if event else "connection"
            else:
                base = "connection"
            return base
        m = cls._CLONE_RE.match(rhs)
        if m:
            recv = m.group("recv")
            if "." in recv and cls._DOTTED_RE.match(recv):
                base = cls._sanitize(recv.rsplit(".", 1)[1])
                return (base + "Clone") if base else "clone"
            return "clone"
        m = cls._GETATTR_RE.match(rhs)
        if m:
            return cls._sanitize(m.group("key"))  # None when junk: skip, no fallback
        m = cls._REQUIRE_RE.match(rhs)
        if m:
            return cls._module_name(m.group("arg"))
        m = cls._NEW_RE.match(rhs)
        if m:
            recv = m.group("recv")
            # factory on a static path (`Instance.new("Part")`); a register
            # receiver (`R9.new`) names nothing by itself: refuse.
            if (cls._DOTTED_RE.match(recv)
                    and not re.search(r"\b[RU]\d+\b", recv)):
                return cls._sanitize(m.group("arg"))
        # A bare closure value carries its own debug name (`dupclosure
        # 'fact'`); a trailing call (`dupclosure 'f'(1)`) is the call's
        # result, not the function, so the anchor keeps it out.
        m = cls._CLOSURE_RE.match(rhs)
        if m:
            return cls._sanitize(m.group("name"))
        # Accessor with one word-literal argument
        # (`game:GetService('Players')` -> `players`): the literal is the
        # retrieved thing's own name. Multi-arg calls, dotted (non-method)
        # calls, and non-word literals (format strings, paths) refuse.
        m = cls._STRARG_RE.match(rhs)
        if m and cls._WORD_RE.match(m.group("lit")):
            return cls._sanitize(m.group("lit"))
        if cls._TABLE_CTOR_RE.match(rhs):
            return "table"  # spelling owner reads `tbl`; braces open nothing else
        m = cls._PURE_KIND_RE.match(rhs)
        if m:
            # A coercion is transparent for naming: the wrapped value names
            # the local (`tostring(inst:GetAttribute("OwnerId"))` ->
            # `ownerId`, `tostring(R0.v)` -> `v`). A bare local/literal arg
            # yields nothing and keeps the kind fallback (`tostring(R1)` ->
            # `str`, `type(R0)` -> `kind`), as does a nested coercion.
            inner = rhs[m.end():]
            if inner.endswith(")"):
                inner = inner[:-1]
            transparent = cls._transparent_arg_name(inner.strip(), upvals)
            if transparent is not None:
                return transparent
            return cls._sanitize("string" if m.group("callee") == "tostring" else "type")
        # Dotted static-path call (`math.abs(-5)` -> `abs`,
        # `table.concat(t)` -> `joined`): same getter/participle/verb
        # policy as the method tier, but an ordinary operation name
        # reads its own tail — the library path makes it unambiguous,
        # where a colon receiver is usually an ephemeral temp. A
        # register anywhere in the path refuses (`R0.foo()` names
        # nothing by itself); case folds have no field tail to read.
        m = cls._DOTTED_CALL_RE.match(rhs)
        if m:
            recv, fn, args = m.group("recv"), m.group("fn"), m.group("args")
            if (cls._DOTTED_RE.match(recv)
                    and not re.search(r"\b[RU]\d+\b", recv)):
                if fn in ("lower", "upper") and not args.strip():
                    return None
                if fn.startswith("Get") and len(fn) > 3:
                    return cls._sanitize(fn[3:])
                lowered = fn[0].lower() + fn[1:] if fn else fn
                if lowered in cls._VERB_PARTICIPLE:
                    return cls._sanitize(cls._VERB_PARTICIPLE[lowered])
                leading = re.match(r"^[a-z]+", lowered)
                leading = leading.group(0) if leading else lowered
                if (lowered in cls._ACTION_VERBS
                        or leading in cls._ACTION_VERBS
                        or len(fn) < 3):
                    return None
                return cls._sanitize(fn)
            return None
        # General method tier (claimed only when nothing above fired).
        m = cls._METHOD_RE.match(rhs)
        if m:
            meth, recv, args = m.group("meth"), m.group("recv"), m.group("args")
            # A case fold is transparent: `name:lower()` is still the
            # name, so it reads the receiver's field tail. A bare
            # register receiver carries no hint: refuse rather than
            # invent one.
            if meth in ("lower", "upper") and not args.strip():
                if "." in recv:
                    return cls._sanitize(recv.rsplit(".", 1)[1])
                return None
            # A getter names its noun (`GetChildren` -> `children`);
            # checked before the verb refuse since `get` itself is a
            # verb (Tovek method_call_hint precedes callee_noun_hint).
            if meth.startswith("Get") and len(meth) > 3:
                return cls._sanitize(meth[3:])
            lowered = meth[0].lower() + meth[1:] if meth else meth
            if lowered in cls._VERB_PARTICIPLE:
                return cls._sanitize(cls._VERB_PARTICIPLE[lowered])
            leading = re.match(r"^[a-z]+", lowered)
            leading = leading.group(0) if leading else lowered
            if (lowered in cls._ACTION_VERBS or leading in cls._ACTION_VERBS
                    or len(meth) < 3):
                return None  # an action, not its result
            return None
        return None

    @classmethod
    def _transparent_arg_name(cls, arg: str, upvals: tuple = ()) -> str | None:
        """Name read through a single-arg coercion (`tostring`/`tonumber`):
        the argument's own evidence, else its dotted field tail. Bare
        locals, literals, and nested coercions yield None (caller falls
        back to the kind name)."""
        if not arg or cls._PURE_KIND_RE.match(arg):
            return None
        base = cls._evidence_name(arg, upvals)
        if base is not None:
            return base
        m = cls._FIELD_TAIL_RE.match(arg)
        if m:
            return cls._sanitize(m.group(1))
        return None

    @classmethod
    def _module_name(cls, arg: str) -> str | None:
        """`require` argument -> module binding (`script.Signal` -> `Signal`,
        `:WaitForChild("N")` -> `N`, `Parent` -> `parentModule`)."""
        arg = arg.strip()
        mw = (re.match(r"^.*:WaitForChild\(\s*['\"](?P<m>[^'\"]+)['\"]\s*\)$", arg)
              or re.match(r"^.*:FindFirst\w*\(\s*['\"](?P<n>[^'\"]+)['\"]\s*\)$", arg))
        if mw:
            key = mw.groupdict().get("n") or mw.groupdict().get("m")
            if key.lower() == "parent":
                return "parentModule"
            return cls._sanitize_preserve(key)
        if cls._DOTTED_RE.match(arg):
            tail = arg.rsplit(".", 1)[1]
            if tail.lower() == "parent":
                return "parentModule"
            return cls._sanitize_preserve(tail)
        return "module"

    # Numeric-for operand roles: `for i = R5, R3, R4 do` reads init, limit,
    # step from three temps the compiler materialized. A temp whose only
    # reads are one role's slots takes the role's name.
    _LOOP_ROLE_NAMES = ("start", "limit", "step")
    _NUMFOR_HEAD_RE = re.compile(r"^for (\S+) = (.*) do$")

    def _loop_operand_name(self, nodes: list[Node], reg: int,
                           defnode: Node, taken: set[str],
                           claimed: set[str]) -> str | None:
        """Spelled role name for a register read only by numeric-for
        header operand slots (`for i = R5, R3, R4 do` -> start/limit/step).
        Returns None unless every mention outside the single def line is a
        numfor operand slot and all slots share one position (a temp
        feeding two roles, or read anywhere else, keeps `R#`). The bare
        role is preferred; when it is taken or already claimed by an
        earlier loop the first free numeric suffix wins (`start2`), so a
        second loop in the same proto de-R-numbers too."""
        pat = f"R{reg}"
        if self._occurs(nodes, reg, skip_ids={id(defnode)},
                        roles=("rhs", "cond", "ret", "compound")):
            return None  # read as a value outside headers: not a pure operand
        roles: list[int] = []
        ok = True

        def walk(lst: list[Node]) -> None:
            nonlocal ok
            for nd in lst:
                if nd.header:
                    m = self._NUMFOR_HEAD_RE.match(nd.header)
                    if m is None:
                        if self._code_mentions(nd.header, reg):
                            ok = False  # genfor/while headers take no roles
                    else:
                        if self._code_mentions(m.group(1), reg):
                            ok = False  # the loop variable itself, not an operand
                            continue
                        parts = [s.strip()
                                 for s in self._split_top(m.group(2), ",")]
                        if len(parts) != 3:
                            if self._code_mentions(nd.header, reg):
                                ok = False
                            continue
                        for pos, op in enumerate(parts):
                            if op == pat:
                                roles.append(pos)
                        rest = " ".join(p for p in parts if p != pat)
                        if self._code_mentions(rest, reg):
                            ok = False
                if nd.then_:
                    walk(nd.then_)
                if nd.else_:
                    walk(nd.else_)
        walk(nodes)
        if not ok or len(set(roles)) != 1:
            return None
        return self._free_spelling(
            self._binding_spelling(self._LOOP_ROLE_NAMES[roles[0]]),
            taken, claimed)

    # -- single-def gate + application --
    def _def_sites(self, nodes: list[Node], reg: int
                   ) -> tuple[list[str], Node | None]:
        """Every site that (re)defines R<reg>: `lhs`, `compound`, `top`
        (`R#..top`), or `hbind` (bound by a `for` header); plus the
        plain-`R# = ...` stmt when exactly one `lhs` site exists."""
        sites: list[str] = []
        defnode: Node | None = None
        for nd in nodes:
            if nd.kind == "stmt" and nd.text:
                if reg in self._lhs_defs(nd.text):
                    sites.append("lhs")
                    defnode = defnode or nd
                mc = compound_match(nd.text)
                if mc is not None and int(mc.group(1)) == reg:
                    sites.append("compound")
                if f"R{reg}..top" in nd.text:
                    sites.append("top")
            if nd.header:
                m = _RFOR_BIND_RE.match(nd.header)
                if m and reg in {int(x[1:]) for x in m.group(1).split(", ")}:
                    sites.append("hbind")
            for sub in (nd.then_, nd.else_):
                if sub:
                    sub_sites, sub_def = self._def_sites(sub, reg)
                    sites.extend(sub_sites)
                    defnode = defnode or sub_def
        return sites, defnode

    def _sub_reg(self, text: str, reg: int, name: str) -> str:
        """Rename R<reg> -> name in a line's code part only: string
        contents and `--` comments are never touched."""
        code, comment = self._code_part(text)
        if code.strip().startswith("--"):
            return text  # whole-line comment (e.g. `-- capture VAL R5`)
        pat = re.compile(rf"\bR{reg}\b")
        code = "".join(pat.sub(name, seg) if not q else seg
                       for seg, q in self._quoted_runs(code))
        return code + (" " + comment if comment else "")

    def _rename_reg(self, nodes: list[Node], reg: int, name: str) -> None:
        for nd in nodes:
            if nd.kind in ("stmt", "return") and nd.text:
                nd.text = self._sub_reg(nd.text, reg, name)
            if nd.cond is not None:
                nd.cond.l = self._sub_reg(nd.cond.l, reg, name)
                nd.cond.r = self._sub_reg(nd.cond.r, reg, name)
            if nd.header:
                nd.header = self._sub_reg(nd.header, reg, name)
            if nd.then_:
                self._rename_reg(nd.then_, reg, name)
            if nd.else_:
                self._rename_reg(nd.else_, reg, name)

    def _taken_names(self, nodes: list[Node]) -> set[str]:
        """Bare identifier tokens spelled anywhere in the proto (fields
        after `.`/`:` excluded): taking one as a binding would capture or
        shadow a read. R#/U# names are excluded (they are being replaced)."""
        taken: set[str] = set()
        pat = re.compile(r"(?<![.:A-Za-z0-9_])[A-Za-z_]\w*")
        def walk(lst: list[Node]) -> None:
            for nd in lst:
                for text in ([nd.text] if nd.text else []):
                    code, _ = self._code_part(text)
                    code = self._strip_upval_annots(code)
                    for seg, q in self._quoted_runs(code):
                        if not q:
                            taken.update(pat.findall(seg))
                if nd.cond is not None:
                    try:
                        taken.update(pat.findall(
                            self._strip_upval_annots(nd.cond.render())))
                    except Exception:
                        pass
                if nd.header:
                    taken.update(pat.findall(self._strip_upval_annots(nd.header)))
                if nd.then_:
                    walk(nd.then_)
                if nd.else_:
                    walk(nd.else_)
        walk(nodes)
        # register names are being replaced, not taken; everything else
        # spelled bare (globals, a future sibling rename) blocks reuse.
        taken = {t for t in taken if not re.fullmatch(r"[RU]\d+", t)}
        taken |= set(self._LUAU_KEYWORDS) | {"self", "_"} | set(self._SOFT_RESERVED)
        return taken

    def _code_mentions(self, text: str, reg: int) -> bool:
        """Whether R<reg> occurs in a line's code part (strings/comments
        excluded): the web-splitter's nested-mention probe."""
        code, _ = self._code_part(text)
        pat = re.compile(rf"\bR{reg}\b")
        return any(pat.search(seg) for seg, q in self._quoted_runs(code) if not q)

    def _subtree_mentions(self, nodes: list[Node], reg: int) -> bool:
        """Whether R<reg> occurs anywhere in a child subtree: flat lines,
        conditions, and headers alike."""
        for nd in nodes:
            if nd.kind in ("stmt", "return") and nd.text \
                    and self._code_mentions(nd.text, reg):
                return True
            if nd.cond is not None:
                try:
                    if re.search(rf"\bR{reg}\b", nd.cond.render()):
                        return True
                except Exception:
                    pass
            if nd.header and self._code_mentions(nd.header, reg):
                return True
            for sub in (nd.then_, nd.else_):
                if sub and self._subtree_mentions(sub, reg):
                    return True
        return False

    def _nested_mentions(self, nodes: list[Node], reg: int) -> bool:
        """Whether R<reg> occurs anywhere below a flat list's top level:
        in a child subtree, a top-level header, or a top-level condition.
        Top-level statement lines are the webs' home, not nested."""
        for nd in nodes:
            if nd.cond is not None:
                try:
                    if re.search(rf"\bR{reg}\b", nd.cond.render()):
                        return True
                except Exception:
                    pass
            if nd.header and self._code_mentions(nd.header, reg):
                return True
            for sub in (nd.then_, nd.else_):
                if sub and self._subtree_mentions(sub, reg):
                    return True
        return False

    @classmethod
    def _strip_upval_annots(cls, text: str) -> str:
        """Blank the `(name)` metadata off upvalue annotations (`U0(seed)`
        -> `U0`): the name is debug info, never a bare-word read, so the
        taken-names probe must not see it. Single owner of the annotation
        shape (with `_UPVAL_RE` above)."""
        return cls._UPVAL_ANNOT_RE.sub("U0", text)

    def _plain_defs(self, lst: list[Node]) -> dict[int, list[tuple[int, str]]]:
        """Plain `R# = ...` defs in a flat list, in order: reg ->
        [(index, rhs)]. Single owner of the web-def shape: the top-level
        and region web passes share it (both exclude `..top`/multret,
        which textual rewriting cannot track)."""
        found: dict[int, list[tuple[int, str]]] = {}
        for i, nd in enumerate(lst):
            if nd.kind == "stmt" and nd.text:
                code, _ = self._code_part(nd.text)
                parts = self._split_lhs_rhs(code)
                if parts is not None:
                    m = re.match(r"^R(\d+)$", parts[0].strip())
                    if (m and "..top" not in parts[1]
                            and "multret" not in parts[1]):
                        found.setdefault(int(m.group(1)), []).append((i, parts[1]))
        return found

    def _apply_web_splits(self, nodes: list[Node], numparams: int,
                          taken: set[str], claimed: set[str],
                          upvals: tuple = ()) -> int:
        """Rename disjoint live ranges of multi-def registers, top-level
        straight-line lists only. Web i of R is `def_i` plus the top-level
        lines up to (not incl.) `def_{i+1}`; each web with evidence-shaped
        RHS takes the single-def gates (spelling, taken, claimed).
        Sound because the top list runs once in order per execution
        (loop depth 0: no back-edge re-entry) and the nested-mention
        probe guarantees every R mention lives in these flat lines:
        uses before `def_1` (entry values) keep `R#`. Only the smallest
        slice: any compound/`..top`/header def, any nested mention, or
        any def outside this list refuses the whole register."""
        # Top-level plain `R# = ...` defs, in order, with their RHS.
        topdefs = self._plain_defs(nodes)
        renamed = 0
        for reg in sorted(topdefs):
            if reg < numparams or len(topdefs[reg]) < 2:
                continue
            sites, _ = self._def_sites(nodes, reg)
            if sites != ["lhs"] * len(topdefs[reg]):
                continue  # a def lives outside this list, or compound/top/bind
            if self._nested_mentions(nodes, reg):
                continue
            webs = topdefs[reg]
            for w, (di, rhs) in enumerate(webs):
                if self._code_mentions(rhs, reg):
                    continue  # self-read: the RHS reads the range renamed away
                base = self._evidence_name(rhs.strip(), upvals)
                if base is None:
                    continue
                name = self._binding_spelling(base)
                if name in taken or name in claimed:
                    continue
                hi = webs[w + 1][0] if w + 1 < len(webs) else len(nodes)
                self._rename_reg(nodes[di:hi], reg, name)
                claimed.add(name)
                renamed += 1
        return renamed

    @staticmethod
    def _straight_region(lst: list[Node]) -> bool:
        """A region list that executes strictly in order: flat statement
        lines and trailing transfers only. No nested control flow, labels,
        or gotos that could enter or leave mid-list."""
        return bool(lst) and all(nd.kind in ("stmt", "return", "break",
                                             "continue") for nd in lst)

    # -- accumulator naming --
    _ACCUM_INITS = (("0", "+=", "total"), ("0", "-=", "total"),
                    ('""', "..=", "buf"), ("''", "..=", "buf"))
    _ACCUM_OP_RE = re.compile(r"^R\d+\s*(\+=|-=|\*=|/=|//=|%=|\^=|\.\.=)")

    def _is_plain_store(self, text: str, reg: int) -> bool:
        """Whether a stmt line plain-assigns R<reg>: `_lhs_defs` covers
        compound writers too, but a compound store accumulates the same
        range rather than ending it."""
        if reg not in self._lhs_defs(text):
            return False
        mc = compound_match(text)
        return not (mc is not None and int(mc.group(1)) == reg)

    def _accum_read(self, sl: list[Node], reg: int,
                    defnode: Node) -> bool:
        """Whether R<reg> is read anywhere inside its live range except
        by its own init and compound stores: a return, condition, plain
        statement, or loop-header read of the accumulated value. A
        compound's read of the store it extends proves nothing (every
        accumulator has one), so compound lines for the register never
        count."""
        for nd in sl:
            if nd is defnode:
                continue
            if nd.kind in ("stmt", "return") and nd.text:
                mc = compound_match(nd.text)
                if mc is not None and int(mc.group(1)) == reg:
                    continue  # own accumulation, not an outside read
                if self._code_mentions(nd.text, reg):
                    return True
            if nd.cond is not None:
                try:
                    if re.search(rf"\bR{reg}\b", nd.cond.render()):
                        return True
                except Exception:
                    pass
            if nd.header and self._code_mentions(nd.header, reg):
                return True
            for sub in (nd.then_, nd.else_):
                if sub and self._accum_read(sub, reg, defnode):
                    return True
        return False

    def _nested_plain_store(self, sl: list[Node], reg: int,
                           defnode: Node) -> bool:
        """Whether R<reg> is plain-assigned anywhere inside a live range
        except by the range's own init line: a nested store would mix a
        second value into the renamed range (nested compounds are the
        accumulation itself and stay)."""
        for nd in sl:
            if nd is not defnode and nd.kind == "stmt" and nd.text:
                if self._is_plain_store(nd.text, reg):
                    return True
            for sub in (nd.then_, nd.else_):
                if sub and self._nested_plain_store(sub, reg, defnode):
                    return True
        return False

    def _apply_accumulators(self, nodes: list[Node], numparams: int,
                            taken: set[str], claimed: set[str]) -> int:
        """Name hot-loop accumulators (`R0 = 0 ... R0 += i * j ...
        return R0` -> `total`; `+= 1` counts -> `count`; `""` plus `..=`
        builds -> `buf`). Each register is renamed only over its own
        live range: the literal init line through (not incl.) its next
        top-level plain store. Within the range there must be at least
        one self-compound (the accumulation: a bare constant keeps `R#`)
        and at least one read outside the register's own stores (dead
        stores keep `R#`); binds and `..top` aliasing refuse outright.
        Per-register spelling plus
        taken/claimed gates, with suffixes past collisions."""
        renamed = 0
        for di, nd in enumerate(nodes):
            if nd.kind != "stmt" or not nd.text:
                continue
            code, _ = self._code_part(nd.text)
            m = _RMULTI_LHS_RE.match(code)
            if not m:
                continue
            lhs = [int(x[1:]) for x in m.group(1).split(", ")]
            if len(lhs) != 1:
                continue
            reg, rhs = lhs[0], code[m.end():].strip()
            if reg < numparams:
                continue
            sites, _ = self._def_sites(nodes, reg)
            if not sites or any(s not in ("lhs", "compound")
                               for s in sites):
                continue  # bound or ..top: not a plain temp
            if self._code_mentions(rhs, reg):
                continue  # self-read: the RHS reads the renamed-away store
            hi = len(nodes)
            for i in range(di + 1, len(nodes)):
                nd2 = nodes[i]
                if nd2.kind == "stmt" and nd2.text \
                        and self._is_plain_store(nd2.text, reg):
                    hi = i
                    break
            sl = nodes[di:hi]
            if self._nested_plain_store(sl, reg, nd):
                continue
            first: tuple[str, str] | None = None

            def find(lst: list[Node]) -> None:
                nonlocal first
                for x in lst:
                    if first is not None:
                        return
                    if x.kind in ("stmt", "return") and x.text:
                        c, _ = self._code_part(x.text)
                        mc = compound_match(c)
                        if mc is not None and int(mc.group(1)) == reg:
                            mo = self._ACCUM_OP_RE.match(c)
                            first = (mo.group(1), c[mo.end():].strip())
                            return
                    for sub in (x.then_, x.else_):
                        if sub:
                            find(sub)
            find(sl)
            if first is None:
                continue  # no accumulation: a bare constant, not a total
            op, step = first
            base = None
            if op == "+=" and rhs == "0" and step == "1":
                base = "count"
            else:
                for lit, o, nm in self._ACCUM_INITS:
                    if rhs == lit and op == o:
                        base = nm
                        break
            if base is None:
                continue  # exotic shape (`//=`, non-zero init): no name
            if not self._accum_read(sl, reg, nd):
                continue  # never read: a dead store, not an accumulator
            name = self._free_spelling(self._binding_spelling(base),
                                       taken, claimed)
            self._rename_reg(sl, reg, name)
            claimed.add(name)
            renamed += 1
        return renamed

    # -- generic-for iterator naming --
    _GENFOR_CALLEES = ("pairs", "ipairs", "next")
    _GENFOR_HEAD_RE = re.compile(r"^for (.*?) in (.*) do$")
    _GENFOR_TRIPLE_RHS_RE = re.compile(r"^(pairs|ipairs|next)\s*\(")
    _GENFOR_VAR_NAMES = ("k", "v")

    def _genfor_triples(
            self, nodes: list[Node]
            ) -> list[tuple[tuple[int, int, int], str, Node]]:
        """Every `R_A, R_B, R_C = pairs/ipairs/next(...)` line at any
        depth, in order: ((A, B, C), callee, defnode). The iterator
        triples the compiler materializes for generic-for loops. A list,
        not a map: adversarial code redefines the same registers with a
        second triple (each triple owns its own live range)."""
        found: list[tuple[tuple[int, int, int], str, Node]] = []

        def walk(lst: list[Node]) -> None:
            for nd in lst:
                if nd.kind == "stmt" and nd.text:
                    code, _ = self._code_part(nd.text)
                    m = _RMULTI_LHS_RE.match(code)
                    if m:
                        regs = [int(x[1:]) for x in m.group(1).split(", ")]
                        if len(regs) == 3 and len(set(regs)) == 3:
                            mc = self._GENFOR_TRIPLE_RHS_RE.match(
                                code[m.end():].strip())
                            if mc:
                                found.append(((regs[0], regs[1], regs[2]),
                                            mc.group(1), nd))
                if nd.then_:
                    walk(nd.then_)
                if nd.else_:
                    walk(nd.else_)
        walk(nodes)
        return found

    def _apply_genfor_triples(self, nodes: list[Node], numparams: int,
                              taken: set[str], claimed: set[str]) -> int:
        """Name iterator triples (`R6, R7, R8 = pairs(R5)` ->
        `pairsFn, pairsTbl, pairsKey`). Compiler temps are routinely
        reused, so each triple register is        renamed only over its own live range: the triple line through (not incl.) its next
        top-level store. Within the range the register must read only from
        its own positional slot of generic-for in-operands, with no
        nested store and no nested `for` bind (a temp feeding two roles
        keeps `R#`). Binds outside the range belong to other ranges and
        stay `R#`; the rename is a pure textual scoping, sound for the
        same reason a whole-tree single-def rename is. A later `R +=`
        bounds the range like a redefinition; `..top` aliasing refuses
        outright. Per-register spelling plus taken/claimed gates, with
        suffixes past collisions (`pairsFn2`)."""
        renamed = 0
        for (a, b, c), callee, defnode in self._genfor_triples(nodes):
            try:
                di = next(i for i, nd in enumerate(nodes) if nd is defnode)
            except StopIteration:
                continue  # nested triple line: ranges need a flat list
            regs = (a, b, c)
            if any(r < numparams for r in regs):
                continue
            code, _ = self._code_part(defnode.text)
            rhs = code[_RMULTI_LHS_RE.match(code).end():]
            plans: list[tuple[int, int]] = []  # (reg, range end)
            for r in regs:
                sites, _ = self._def_sites(nodes, r)
                if not sites or any(s not in ("lhs", "compound", "hbind")
                                   for s in sites):
                    continue  # ..top: not a plain temp
                if self._code_mentions(rhs, r):
                    continue  # self-read: the RHS reads the renamed-away store
                hi = len(nodes)
                for i in range(di + 1, len(nodes)):
                    nd = nodes[i]
                    if nd.kind == "stmt" and nd.text \
                            and r in self._lhs_defs(nd.text):
                        hi = i
                        break
                plans.append((r, hi))
            want = {a: 0, b: 1, c: 2}
            for r, hi in plans:
                if self._nested_triple_def(nodes[di:hi], r, defnode):
                    continue
                positions: list[int] = []
                ok = True

                def walk(lst: list[Node]) -> None:
                    nonlocal ok
                    for nd in lst:
                        if nd.header:
                            m = self._GENFOR_HEAD_RE.match(nd.header)
                            if m is None:
                                if self._code_mentions(nd.header, r):
                                    ok = False
                            else:
                                parts = [s.strip() for s in
                                         self._split_top(m.group(2), ",")]
                                if len(parts) != 3:
                                    if self._code_mentions(nd.header, r):
                                        ok = False
                                else:
                                    for pos, op in enumerate(parts):
                                        if op == f"R{r}":
                                            if pos != want[r]:
                                                ok = False
                                            else:
                                                positions.append(pos)
                                        elif self._code_mentions(op, r):
                                            ok = False  # inside a complex operand
                                    if self._code_mentions(m.group(1), r):
                                        ok = False  # bound, not fed: belt, hbind refused
                        if nd.then_:
                            walk(nd.then_)
                        if nd.else_:
                            walk(nd.else_)
                walk(nodes[di:hi])
                if not ok or not positions:
                    continue
                name = self._free_spelling(
                    self._binding_spelling(callee + ("Fn", "Tbl", "Key")[want[r]]),
                    taken, claimed)
                self._rename_reg(nodes[di:hi], r, name)
                claimed.add(name)
                renamed += 1
        return renamed

    def _nested_triple_def(self, sl: list[Node], reg: int,
                           defnode: Node) -> bool:
        """Whether R<reg> is stored or bound anywhere inside a triple's
        live range except by the triple line itself (`_lhs_defs` covers
        plain and compound stores, `_RFOR_BIND_RE` any `for` bind): a
        nested store or bind would mix a second value into the renamed
        range."""
        for nd in sl:
            if nd is not defnode:
                if nd.kind == "stmt" and nd.text:
                    if reg in self._lhs_defs(nd.text):
                        return True
                if nd.header:
                    m = _RFOR_BIND_RE.match(nd.header)
                    if m and reg in {int(x[1:])
                                     for x in m.group(1).split(", ")}:
                        return True
            for sub in (nd.then_, nd.else_):
                if sub and self._nested_triple_def(sub, reg, defnode):
                    return True
        return False

    def _apply_genfor_vars(self, nodes: list[Node], numparams: int,
                           taken: set[str], claimed: set[str]) -> int:
        """Name generic-for loop variables (`for R5, R6 in ...` -> k, v):
        slot 0 is the key, slot 1 the value, a lone variable the value;
        slots 2+ stay numbered. The rename is scoped to the owning
        loops (their bind slots plus their bodies): reads outside belong
        to other live ranges and stay `R#`, so reuse elsewhere never
        vetoes. Refuses only `..top`/multret machinery the text backend
        keys on register numbers. Per-variable spelling plus taken/
        claimed gates, with suffixes past collisions (`k2`)."""
        owners: dict[int, list[Node]] = {}

        def collect(lst: list[Node]) -> None:
            for nd in lst:
                if nd.header:
                    m = self._GENFOR_HEAD_RE.match(nd.header)
                    if m:
                        for tok in m.group(1).split(","):
                            tok = tok.strip()
                            if re.fullmatch(r"R\d+", tok):
                                owners.setdefault(int(tok[1:]), []).append(nd)
                if nd.then_:
                    collect(nd.then_)
                if nd.else_:
                    collect(nd.else_)
        collect(nodes)
        renamed = 0
        for reg in sorted(owners):
            if reg < numparams:
                continue  # parameter slot: entry value, not a loop variable
            own = owners[reg]
            names: set[str] = set()
            bad = False
            for nd in own:
                toks = [t.strip() for t in self._GENFOR_HEAD_RE.match(
                    nd.header).group(1).split(",")]
                idx = [i for i, t in enumerate(toks) if t == f"R{reg}"]
                if len(idx) != 1:
                    bad = True
                    break
                if len(toks) == 1:
                    names.add("v")
                elif idx[0] < len(self._GENFOR_VAR_NAMES):
                    names.add(self._GENFOR_VAR_NAMES[idx[0]])
                else:
                    bad = True
                    break
            if bad or len(names) != 1:
                continue  # bound twice in one header, shifted slots, or slot 2+
            if any(self._genfor_machinery(nd.then_, reg)
                   or self._genfor_machinery(nd.else_, reg) for nd in own):
                continue  # backend machinery keys on the register number
            base = names.pop()
            name = self._free_spelling(self._binding_spelling(base),
                                       taken, claimed)
            for nd in own:
                m = self._GENFOR_HEAD_RE.match(nd.header)
                toks = [name if t.strip() == f"R{reg}" else t.strip()
                        for t in m.group(1).split(",")]
                nd.header = "for " + ", ".join(toks) + " in " \
                    + m.group(2) + " do"
                self._rename_reg(nd.then_, reg, name)
                self._rename_reg(nd.else_, reg, name)
            claimed.add(name)
            renamed += 1
        return renamed

    def _genfor_machinery(self, lst: list[Node], reg: int) -> bool:
        """Whether R<reg> touches `..top`/multret machinery in a loop
        body: the text backend keys those forms on register numbers, so
        a renamed variable would stop parsing as the same form. String
        contents never count (a `"multret"` literal is not machinery)."""
        for nd in lst:
            if nd.kind in ("stmt", "return") and nd.text:
                code, _ = self._code_part(nd.text)
                for seg, quoted in self._quoted_runs(code):
                    if quoted:
                        continue
                    if f"R{reg}..top" in seg:
                        return True
                    if "multret" in seg and re.search(rf"\bR{reg}\b", seg):
                        return True
            for sub in (nd.then_, nd.else_):
                if sub and self._genfor_machinery(sub, reg):
                    return True
        return False

    def _apply_region_webs(self, nodes: list[Node], numparams: int,
                           taken: set[str], claimed: set[str],
                           upvals: tuple = ()) -> int:
        """Rename webs inside one-level nested straight-line regions:
        loop bodies and top-level if branches (elseif chains descended).
        Each region executes its lines in order, so per-def slices are
        sound the way top-level webs are, plus two region gates. Loops
        repeat: exactly one def per body, first R mention (entry-value
        protection — a pre-def read sees the incoming/back-edge value,
        not this web), and the loop cond/header must not read R (they
        would read the renamed-away register). If branches run once:
        one web per def, pre-def reads keep `R#` (entry value, untouched).
        Outside the region only earlier top-level lines, the owner if's
        conds, and sibling branches may mention R: anything after the
        owner (a reader of this web's value) or in a loop cond/header
        refuses. No growth: renames rewrite text in place; refusal is
        the rollback. Reuses the single-def gates (spelling, taken,
        claimed) per web."""
        renamed = 0
        for oi, nd in enumerate(nodes):
            jobs: list[tuple[list[Node], bool]] = []  # (region, is_loop)
            if nd.kind in ("while", "repeat", "numfor", "genfor"):
                if self._straight_region(nd.then_):
                    jobs.append((nd.then_, True))
            elif nd.kind == "if":
                cur = nd
                while True:
                    if self._straight_region(cur.then_):
                        jobs.append((cur.then_, False))
                    els = cur.else_
                    if len(els) == 1 and els[0].kind == "if":
                        cur = els[0]
                        continue
                    if self._straight_region(els):
                        jobs.append((els, False))
                    break
            for region, is_loop in jobs:
                renamed += self._rename_region_webs(
                    nodes, oi, nd, region, is_loop,
                    numparams, taken, claimed, upvals)
        return renamed

    def _rename_region_webs(self, nodes: list[Node], oi: int, owner: Node,
                            region: list[Node], is_loop: bool,
                            numparams: int, taken: set[str],
                            claimed: set[str], upvals: tuple = ()) -> int:
        # Plain `R# = ...` defs in the region, in order.
        rdefs = self._plain_defs(region)
        renamed = 0
        for reg in sorted(rdefs):
            if reg < numparams:
                continue  # parameter slot: entry value, not a definition
            if is_loop and len(rdefs[reg]) != 1:
                continue  # the back edge would mix web values into one range
            sites, _ = self._def_sites(nodes, reg)
            if any(s != "lhs" for s in sites):
                continue  # compound/..top/header writers: rewrite is unsafe
            if self._subtree_mentions(nodes[oi + 1:], reg):
                continue  # a later reader may see this web's value: keep R#
            if is_loop:
                if owner.cond is not None:
                    try:
                        if re.search(rf"\bR{reg}\b", owner.cond.render()):
                            continue
                    except Exception:
                        pass
                if owner.header and self._code_mentions(owner.header, reg):
                    continue
                di, rhs = rdefs[reg][0]
                if any(rn.text and self._code_mentions(rn.text, reg)
                       for rn in region[:di]):
                    continue  # entry-value protection
                webs = [(di, rhs)]
            else:
                webs = rdefs[reg]
            for w, (di, rhs) in enumerate(webs):
                if self._code_mentions(rhs, reg):
                    continue  # self-read: the RHS reads the range renamed away
                base = self._evidence_name(rhs.strip(), upvals)
                if base is None:
                    continue
                name = self._binding_spelling(base)
                if name in taken or name in claimed:
                    continue  # capture/shadow: leave the register
                hi = webs[w + 1][0] if w + 1 < len(webs) else len(region)
                self._rename_reg(region[di:hi], reg, name)
                claimed.add(name)
                renamed += 1
        return renamed

    def _apply_names(self, nodes: list[Node], numparams: int = 0,
                     upvals: tuple = ()) -> int:
        """One rename pass over the finished tree: disjoint webs of
        multi-def registers first (top-level straight-line lists), then
        single-def leftovers, then one-level nested region webs. `upvals` is the proto's debug upvalue table
        (empty when stripped): it names `R = U0` loads, and nothing else
        reads it. Deterministic (regs in numeric order); a
        refused candidate leaves its register untouched. Registers below
        `numparams` are never renamed: they hold the caller's values on
        entry, so a same-numbered store is a reassignment, not a
        definition a textual rewrite requires."""
        regs: set[int] = set()
        def collect(lst: list[Node]) -> None:
            for nd in lst:
                if nd.kind == "stmt" and nd.text:
                    regs.update(self._lhs_defs(nd.text))
                if nd.then_:
                    collect(nd.then_)
                if nd.else_:
                    collect(nd.else_)
        collect(nodes)
        taken = self._taken_names(nodes)
        claimed: set[str] = set()
        # Webs first: a renamed web's lines no longer define `R#`, so the
        # single-def loop below sees only genuinely single leftover webs.
        # Regions run last: they must not steal a contested name from an
        # earlier pass (single-def keeps its existing outputs byte-stable).
        renamed = self._apply_web_splits(nodes, numparams, taken, claimed,
                                         upvals)
        for reg in sorted(regs):
            if reg < numparams:
                continue  # parameter slot: entry value, not a definition
            sites, defnode = self._def_sites(nodes, reg)
            if sites != ["lhs"] or defnode is None:
                continue
            code, _ = self._code_part(defnode.text)
            parts = self._split_lhs_rhs(code)
            if parts is None:
                continue
            lhs, rhs = parts
            if lhs.strip() != f"R{reg}" or "..top" in rhs or "multret" in rhs:
                continue  # multi-def line or multret: not a single value
            if self._code_mentions(rhs, reg):
                continue  # self-read (`R5 = tostring(R5)`): the RHS reads
                # the entry value, which renaming would replace with nil
            base = self._evidence_name(rhs.strip(), upvals)
            if base is None:
                # No RHS evidence: a temp feeding one numeric-for role
                # (`for i = R5, R3, R4 do`) still names by its use, with
                # suffixes past taken/claimed roles (`start2`).
                name = self._loop_operand_name(nodes, reg, defnode,
                                               taken, claimed)
                if name is None:
                    continue
            else:
                name = self._binding_spelling(base)
                if name in taken or name in claimed:
                    continue  # capture/shadow: evidence names never suffix
            self._rename_reg(nodes, reg, name)
            claimed.add(name)
            renamed += 1
        renamed += self._apply_genfor_triples(nodes, numparams, taken,
                                              claimed)
        renamed += self._apply_genfor_vars(nodes, numparams, taken, claimed)
        renamed += self._apply_accumulators(nodes, numparams, taken,
                                            claimed)
        renamed += self._apply_region_webs(nodes, numparams, taken, claimed,
                                           upvals)
        return renamed
