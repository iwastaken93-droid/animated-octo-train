"""Luau v14 disassembler. Output mirrors `luau-compile --text` (dumpInstruction).

Usage: python disassembler_v14.py fixtures/v14/trivial.luac
"""
from __future__ import annotations
import sys
import opcodes_v14 as op
import parser_v14


class DisasmError(Exception):
    pass


def split(code: list[int]) -> list[tuple[int, str, int, int | None]]:
    """Split raw words into (pc, opname, header, aux). Verifies accounting."""
    out = []
    pc = 0
    n = len(code)
    while pc < n:
        insn = code[pc]
        oi = op.op(insn)
        if oi >= op.COUNT:
            raise DisasmError(f"bad opcode {oi} at pc {pc}")
        oname = op.NAMES[oi]
        ln = op.get_op_length(oi)
        if pc + ln > n:
            raise DisasmError(f"truncated {oname} at pc {pc} (need {ln} words)")
        aux = code[pc + 1] if ln == 2 else None
        out.append((pc, oname, insn, aux))
        pc += ln
    return out


def _fmt_str(bs: bytes) -> str:
    try:
        s = bs.decode("utf-8")
    except Exception:
        return repr(bs)
    if all(ord(ch) >= 32 or ch in "\t" for ch in s):
        return f"'{s}'" if len(s) < 32 else f"'{s[:32]}'..."
    return "'" + "".join(f"\\x{ord(ch):02X}" if ord(ch) < 32 else ch for ch in s[:32]) + ("'..." if len(s) >= 32 else "'")


def _fmt_num(v: float) -> str:
    return "%.17g" % float(v)


def _fmt_vec(vals, fmt: str) -> str:
    vals = list(vals)
    if vals[3] == 0:
        vals = vals[:3]
    return ", ".join(fmt % x for x in vals)


def _child_name(bc: parser_v14.Bytecode, fid: int) -> str:
    # mirror dumpConstant: empty when the child proto is anonymous
    if 0 <= fid < len(bc.protos):
        dn = bc.protos[fid].debugname
        if dn is not None:
            return dn.decode("utf-8", "replace") if isinstance(dn, bytes) else str(dn)
        return ""
    return f"#{fid}"


def fmt_const(bc: parser_v14.Bytecode, proto: parser_v14.Proto, kid: int) -> str:
    c = proto.consts[kid]
    t, v = c.tag, c.value
    if t == parser_v14.LBC_CONSTANT_NIL:
        return "nil"
    if t == parser_v14.LBC_CONSTANT_BOOLEAN:
        return "true" if v else "false"
    if t == parser_v14.LBC_CONSTANT_NUMBER:
        return _fmt_num(v)
    if t == parser_v14.LBC_CONSTANT_INTEGER:
        return str(v)
    if t == parser_v14.LBC_CONSTANT_STRING:
        return _fmt_str(v[2])
    if t == parser_v14.LBC_CONSTANT_IMPORT:
        _, iid, (count, id0, id1, id2) = v
        parts = []
        for i in [id0, id1, id2][:count]:
            sc = proto.consts[i]
            parts.append(sc.value[2].decode("utf-8", "replace"))
        return ".".join(parts)
    if t in (parser_v14.LBC_CONSTANT_TABLE, parser_v14.LBC_CONSTANT_TABLE_WITH_CONSTANTS):
        return "{...}"
    if t == parser_v14.LBC_CONSTANT_CLOSURE:
        nm = _child_name(bc, v[1])
        return f"'{nm}'" if nm else ""
    if t == parser_v14.LBC_CONSTANT_VECTOR:
        return _fmt_vec(v, "%.9g")
    if t == parser_v14.LBC_CONSTANT_VECTORD:
        return _fmt_vec(v, "%.17g")
    if t == parser_v14.LBC_CONSTANT_CLASS_SHAPE:
        return f"classshape{list(v[1:])}"
    return f"tag{t}"


def _target(insns: list, idx: int) -> int | None:
    pc, oname, insn, aux = insns[idx]
    return op.get_jump_target(insn, aux or 0, pc)


def disassemble_proto(bc: parser_v14.Bytecode, proto: parser_v14.Proto) -> str:
    insns = split(proto.code)
    # label assignment (dumpCurrentFunction): sequential ids for all jump targets
    labels: dict[int, int] = {}
    order: list[int] = []
    for i, (pc, _, insn, aux) in enumerate(insns):
        t = op.get_jump_target(insn, aux or 0, pc)
        if t is not None and t not in labels:
            labels[t] = -1
            order.append(t)
    for k, t in enumerate(sorted(order)):
        labels[t] = k

    lines: list[str] = []
    for pc, oname, insn, aux in insns:
        if oname == "PREPVARARGS":
            continue  # hidden in --text output, like the reference
        A, B, C, D, E = op.a(insn), op.b(insn), op.c(insn), op.d(insn), op.e(insn)
        pre = f"L{labels[pc]}: " if pc in labels else ""
        t = op.get_jump_target(insn, aux or 0, pc)
        L = f"L{labels[t]}" if t is not None and t in labels else (f"@{t}" if t is not None else "?")

        if oname == "LOADNIL":
            s = f"LOADNIL R{A}"
        elif oname == "LOADB":
            s = f"LOADB R{A} {B} +{C}" if C else f"LOADB R{A} {B}"
        elif oname == "LOADN":
            s = f"LOADN R{A} {D}"
        elif oname == "LOADK":
            s = f"LOADK R{A} K{D} [{fmt_const(bc, proto, D)}]"
        elif oname == "MOVE":
            s = f"MOVE R{A} R{B}"
        elif oname in ("GETGLOBAL", "SETGLOBAL"):
            s = f"{oname} R{A} K{aux} [{fmt_const(bc, proto, aux)}]"
        elif oname in ("GETUPVAL", "SETUPVAL"):
            s = f"{oname} R{A} U{B}"
        elif oname == "CLOSEUPVALS":
            s = f"CLOSEUPVALS R{A}"
        elif oname == "GETIMPORT":
            s = f"GETIMPORT R{A} {D} [{fmt_const(bc, proto, D)}]"
        elif oname in ("GETTABLE", "SETTABLE", "ADD", "SUB", "MUL", "DIV", "IDIV", "MOD", "POW",
                        "AND", "OR", "CONCAT"):
            s = f"{oname} R{A} R{B} R{C}"
        elif oname in ("GETTABLEKS", "SETTABLEKS"):
            s = f"{oname} R{A} R{B} K{aux} [{fmt_const(bc, proto, aux)}]"
        elif oname in ("GETTABLEN", "SETTABLEN"):
            s = f"{oname} R{A} R{B} {C + 1}"
        elif oname == "NEWCLOSURE":
            s = f"NEWCLOSURE R{A} P{D}"
        elif oname in ("NAMECALL", "NAMECALLUDATA", "GETUDATAKS", "SETUDATAKS"):
            s = f"{oname} R{A} R{B} K{aux & 0xFFFF} [{fmt_const(bc, proto, aux & 0xFFFF)}]"
        elif oname == "CALL":
            s = f"CALL R{A} {B - 1} {C - 1}"
        elif oname == "CALLFB":
            s = f"CALLFB R{A} {B - 1} {C - 1} [{aux}]"
        elif oname == "RETURN":
            s = f"RETURN R{A} {B - 1}"
        elif oname in ("JUMP", "JUMPBACK", "JUMPX"):
            s = f"{oname} {L}"
        elif oname in ("JUMPIF", "JUMPIFNOT"):
            s = f"{oname} R{A} {L}"
        elif oname in ("JUMPIFEQ", "JUMPIFLE", "JUMPIFLT", "JUMPIFNOTEQ", "JUMPIFNOTLE", "JUMPIFNOTLT"):
            s = f"{oname} R{A} R{aux} {L}"
        elif oname in ("ADDK", "SUBK", "MULK", "DIVK", "IDIVK", "MODK", "POWK", "ANDK", "ORK"):
            s = f"{oname} R{A} R{B} K{C} [{fmt_const(bc, proto, C)}]"
        elif oname in ("SUBRK", "DIVRK"):
            s = f"{oname} R{A} K{B} [{fmt_const(bc, proto, B)}] R{C}"
        elif oname in ("NOT", "MINUS", "LENGTH"):
            s = f"{oname} R{A} R{B}"
        elif oname == "NEWTABLE":
            arr = aux or 0
            s = f"NEWTABLE R{A} {0 if B == 0 else 1 << (B - 1)} {arr}"
        elif oname == "DUPTABLE":
            s = f"DUPTABLE R{A} {D}"
        elif oname == "SETLIST":
            s = f"SETLIST R{A} R{B} {C - 1} [{aux}]"
        elif oname in ("FORNPREP", "FORNLOOP", "FORGPREP", "FORGPREP_INEXT", "FORGPREP_NEXT"):
            s = f"{oname} R{A} {L}"
        elif oname == "FORGLOOP":
            extra = " [inext]" if (aux is not None and aux & 0x80000000) else ""
            s = f"FORGLOOP R{A} {L} {aux & 0xFF}{extra}"
        elif oname in ("FASTCALL", "FASTPCALL", "FINCONSTRUCT"):
            s = f"{oname} {A} {L}" if oname == "FASTPCALL" and A in (0, 1) else f"{oname} {A} {L}" if oname == "FASTCALL" else f"{oname} R{A} {L}"
            if oname == "FASTPCALL":
                s = f"FASTPCALL {'pcall' if A == 0 else 'xpcall'} {L}"
            elif oname == "FINCONSTRUCT":
                s = f"FINCONSTRUCT R{A} {L}"
        elif oname == "FASTCALL1":
            s = f"FASTCALL1 {A} R{B} {L}"
        elif oname == "FASTCALL2":
            s = f"FASTCALL2 {A} R{B} R{aux} {L}"
        elif oname == "FASTCALL2K":
            s = f"FASTCALL2K {A} R{B} K{aux} {L} [{fmt_const(bc, proto, aux)}]"
        elif oname == "FASTCALL3":
            s = f"FASTCALL3 {A} R{B} R{aux & 0xFF} R{(aux >> 8) & 0xFF} {L}"
        elif oname == "GETVARARGS":
            s = f"GETVARARGS R{A} {B - 1}"
        elif oname == "DUPCLOSURE":
            s = f"DUPCLOSURE R{A} K{D} [{fmt_const(bc, proto, D)}]"
        elif oname == "LOADKX":
            s = f"LOADKX R{A} K{aux} [{fmt_const(bc, proto, aux)}]"
        elif oname == "COVERAGE":
            s = "COVERAGE"
        elif oname == "CAPTURE":
            kind = op.CAPTURE_NAMES.get(A, str(A))
            s = f"CAPTURE {kind} {'U' if A == op.LCT_UPVAL else 'R'}{B}"
        elif oname == "JUMPXEQKNIL":
            s = f"JUMPXEQKNIL R{A} {L}{' NOT' if aux >> 31 else ''}"
        elif oname == "JUMPXEQKB":
            s = f"JUMPXEQKB R{A} {aux & 1} {L}{' NOT' if aux >> 31 else ''}"
        elif oname in ("JUMPXEQKN", "JUMPXEQKS"):
            s = f"{oname} R{A} K{aux & 0xFFFFFF} {L}{' NOT' if aux >> 31 else ''} [{fmt_const(bc, proto, aux & 0xFFFFFF)}]"
        elif oname == "NEWCLASSMEMBER":
            s = f"NEWCLASSMEMBER R{A} R{C} [{fmt_const(bc, proto, aux)}]"
        elif oname == "CMPPROTO":
            s = f"CMPPROTO R{A} #{aux} {L}"
        elif oname == "NEWCLASS":
            base = "no_base" if B == 0xFF else f"R{B}"
            s = f"NEWCLASS R{A} {base} K{aux} {C} [{fmt_const(bc, proto, aux)}]"
        elif oname == "CONSTRUCT":
            s = f"CONSTRUCT R{A} R{B} slot {aux}"
        elif oname in ("NOP", "BREAK", "NATIVECALL"):
            s = oname
        else:
            s = f"{oname} R{A} R{B} R{C} (aux={aux})"
        lines.append(pre + s)
    return "\n".join(lines)


def disassemble(bc: parser_v14.Bytecode) -> str:
    out = []
    for p in bc.protos:
        dn = p.debugname
        label = dn.decode("utf-8", "replace") if isinstance(dn, bytes) else (dn if dn else "??")
        out.append(f"Function {p.id} ({label}):")
        out.append(disassemble_proto(bc, p))
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    files = sys.argv[1:] or ["fixtures/v14/trivial.luac"]
    bad = False
    for f in files:
        try:
            bc = parser_v14.parse(open(f, "rb").read())
            print(f"== {f} ==")
            print(disassemble(bc))
        except (parser_v14.ParseError, DisasmError) as e:
            bad = True
            print(f"FAIL {f}: {e}")
    raise SystemExit(1 if bad else 0)
