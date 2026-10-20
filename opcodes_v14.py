"""Luau v14 opcode metadata. Mirrors luau/Common/include/Luau/Bytecode.h + BytecodeUtils.h.

Word layout (little-endian u32, opcode in low byte):
  ABC: op:8 A:8 B:8 C:8
  AD:  op:8 A:8 D:signed16
  E:   op:8 E:signed24
Some instructions have 1 AUX u32 following the header (length 2).
"""
from __future__ import annotations

NAMES = [
    "NOP",            # 0
    "BREAK",          # 1
    "LOADNIL",        # 2
    "LOADB",          # 3
    "LOADN",          # 4
    "LOADK",          # 5
    "MOVE",           # 6
    "GETGLOBAL",      # 7
    "SETGLOBAL",      # 8
    "GETUPVAL",       # 9
    "SETUPVAL",       # 10
    "CLOSEUPVALS",    # 11
    "GETIMPORT",      # 12
    "GETTABLE",       # 13
    "SETTABLE",       # 14
    "GETTABLEKS",     # 15
    "SETTABLEKS",     # 16
    "GETTABLEN",      # 17
    "SETTABLEN",      # 18
    "NEWCLOSURE",     # 19
    "NAMECALL",       # 20
    "CALL",           # 21
    "RETURN",         # 22
    "JUMP",           # 23
    "JUMPBACK",       # 24
    "JUMPIF",         # 25
    "JUMPIFNOT",      # 26
    "JUMPIFEQ",       # 27
    "JUMPIFLE",       # 28
    "JUMPIFLT",       # 29
    "JUMPIFNOTEQ",    # 30
    "JUMPIFNOTLE",    # 31
    "JUMPIFNOTLT",    # 32
    "ADD",            # 33
    "SUB",            # 34
    "MUL",            # 35
    "DIV",            # 36
    "MOD",            # 37
    "POW",            # 38
    "ADDK",           # 39
    "SUBK",           # 40
    "MULK",           # 41
    "DIVK",           # 42
    "MODK",           # 43
    "POWK",           # 44
    "AND",            # 45
    "OR",             # 46
    "ANDK",           # 47
    "ORK",            # 48
    "CONCAT",         # 49
    "NOT",            # 50
    "MINUS",          # 51
    "LENGTH",         # 52
    "NEWTABLE",       # 53
    "DUPTABLE",       # 54
    "SETLIST",        # 55
    "FORNPREP",       # 56
    "FORNLOOP",       # 57
    "FORGLOOP",       # 58
    "FORGPREP_INEXT", # 59
    "FASTCALL3",      # 60
    "FORGPREP_NEXT",  # 61
    "NATIVECALL",     # 62
    "GETVARARGS",     # 63
    "DUPCLOSURE",     # 64
    "PREPVARARGS",    # 65
    "LOADKX",         # 66
    "JUMPX",          # 67
    "FASTCALL",       # 68
    "COVERAGE",       # 69
    "CAPTURE",        # 70
    "SUBRK",          # 71
    "DIVRK",          # 72
    "FASTCALL1",      # 73
    "FASTCALL2",      # 74
    "FASTCALL2K",     # 75
    "FORGPREP",       # 76
    "JUMPXEQKNIL",    # 77
    "JUMPXEQKB",      # 78
    "JUMPXEQKN",      # 79
    "JUMPXEQKS",      # 80
    "IDIV",           # 81
    "IDIVK",          # 82
    "GETUDATAKS",     # 83
    "SETUDATAKS",     # 84
    "NAMECALLUDATA",  # 85
    "NEWCLASSMEMBER", # 86
    "CALLFB",         # 87
    "CMPPROTO",       # 88
    "FASTPCALL",      # 89
    "NEWCLASS",       # 90
    "CONSTRUCT",      # 91
    "FINCONSTRUCT",   # 92
]
COUNT = len(NAMES)
assert COUNT == 93, COUNT
NAME_TO_OP = {n: i for i, n in enumerate(NAMES)}

# Instructions with an AUX word (getOpLength() == 2 in BytecodeUtils.h)
_HAS_AUX = {
    "GETGLOBAL", "SETGLOBAL", "GETIMPORT",
    "GETTABLEKS", "SETTABLEKS", "NAMECALL",
    "JUMPIFEQ", "JUMPIFLE", "JUMPIFLT", "JUMPIFNOTEQ", "JUMPIFNOTLE", "JUMPIFNOTLT",
    "NEWTABLE", "SETLIST", "FORGLOOP",
    "LOADKX", "FASTCALL2", "FASTCALL2K", "FASTCALL3",
    "JUMPXEQKNIL", "JUMPXEQKB", "JUMPXEQKN", "JUMPXEQKS",
    "GETUDATAKS", "SETUDATAKS", "NAMECALLUDATA",
    "NEWCLASSMEMBER", "CALLFB", "CMPPROTO",
    "NEWCLASS", "CONSTRUCT",
}

# Header encoding. E: signed 24-bit; AD: A + signed 16-bit; else ABC.
_E_MODE = {"JUMPX", "COVERAGE"}
_AD_MODE = {
    "LOADN", "LOADK", "GETIMPORT", "NEWCLOSURE", "DUPTABLE", "DUPCLOSURE",
    "JUMP", "JUMPBACK", "JUMPIF", "JUMPIFNOT",
    "JUMPIFEQ", "JUMPIFLE", "JUMPIFLT", "JUMPIFNOTEQ", "JUMPIFNOTLE", "JUMPIFNOTLT",
    "FORNPREP", "FORNLOOP", "FORGLOOP", "FORGPREP_INEXT", "FORGPREP_NEXT", "FORGPREP",
    "PREPVARARGS", "LOADKX",
    "JUMPXEQKNIL", "JUMPXEQKB", "JUMPXEQKN", "JUMPXEQKS", "CMPPROTO",
}


def op(insn: int) -> int:
    return insn & 0xFF


def name(insn: int) -> str:
    o = op(insn)
    return NAMES[o] if o < COUNT else f"OP_{o}"


def a(insn: int) -> int:
    return (insn >> 8) & 0xFF


def b(insn: int) -> int:
    return (insn >> 16) & 0xFF


def c(insn: int) -> int:
    return (insn >> 24) & 0xFF


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _s24(v: int) -> int:
    v &= 0xFFFFFF
    return v - 0x1000000 if v & 0x800000 else v


def d(insn: int) -> int:
    return _s16(insn >> 16)


def e(insn: int) -> int:
    return _s24(insn >> 8)


def mode(opname: str) -> str:
    if opname in _E_MODE:
        return "E"
    if opname in _AD_MODE:
        return "AD"
    return "ABC"


def has_aux(opname: str) -> bool:
    return opname in _HAS_AUX


def get_op_length(opindex: int) -> int:
    return 2 if NAMES[opindex] in _HAS_AUX else 1


# Capture types (LuauCaptureType)
LCT_VAL = 0
LCT_REF = 1
LCT_UPVAL = 2
CAPTURE_NAMES = {0: "VAL", 1: "REF", 2: "UPVAL"}

# Fastcall ids that take over control flow (isFastCall in BytecodeUtils.h)
_FASTCALL_OPS = {"FASTCALL", "FASTCALL1", "FASTCALL2", "FASTCALL2K", "FASTCALL3", "FASTPCALL", "FINCONSTRUCT"}
# D-jump ops (isJumpD)
_JUMPD_OPS = {
    "JUMP", "JUMPIF", "JUMPIFNOT",
    "JUMPIFEQ", "JUMPIFLE", "JUMPIFLT", "JUMPIFNOTEQ", "JUMPIFNOTLE", "JUMPIFNOTLT",
    "FORNPREP", "FORNLOOP", "FORGPREP", "FORGLOOP", "FORGPREP_INEXT", "FORGPREP_NEXT",
    "JUMPBACK", "JUMPXEQKNIL", "JUMPXEQKB", "JUMPXEQKN", "JUMPXEQKS", "CMPPROTO",
}


def get_jump_target(insn: int, aux: int, pc: int) -> int | None:
    """Mirror of getJumpTarget() in BytecodeUtils.h. pc = word index of header."""
    n = name(insn)
    if n in _JUMPD_OPS:
        return pc + d(insn) + 1
    if n in _FASTCALL_OPS:
        return pc + c(insn) + 2
    if n == "LOADB" and c(insn):
        return pc + c(insn) + 1
    if n == "JUMPX":
        return pc + e(insn) + 1
    return None


def is_fallthrough(opindex: int) -> bool:
    return NAMES[opindex] not in (
        "RETURN", "JUMP", "JUMPBACK", "JUMPX", "FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT",
    )
