"""Luau v14 bytecode parser. Mirrors luau/VM/src/lvmload.cpp + BytecodeBuilder.cpp."""
from __future__ import annotations
from dataclasses import dataclass, field
import struct

LBC_VERSION_MIN = 3
LBC_VERSION_MAX = 14
LPF_INLINABLE = 1 << 3

# constant tags (Bytecode.h)
LBC_CONSTANT_NIL = 0
LBC_CONSTANT_BOOLEAN = 1
LBC_CONSTANT_NUMBER = 2
LBC_CONSTANT_STRING = 3
LBC_CONSTANT_IMPORT = 4
LBC_CONSTANT_TABLE = 5
LBC_CONSTANT_CLOSURE = 6
LBC_CONSTANT_VECTOR = 7
LBC_CONSTANT_TABLE_WITH_CONSTANTS = 8
LBC_CONSTANT_INTEGER = 9
LBC_CONSTANT_CLASS_SHAPE = 10
LBC_CONSTANT_VECTORD = 11


class ParseError(Exception):
    pass


class Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.o = 0

    def eof(self):
        return self.o >= len(self.d)

    def u8(self) -> int:
        if self.o >= len(self.d):
            raise ParseError("unexpected EOF reading u8")
        v = self.d[self.o]
        self.o += 1
        return v

    def u32(self) -> int:
        if self.o + 4 > len(self.d):
            raise ParseError("unexpected EOF reading u32")
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def i32(self) -> int:
        if self.o + 4 > len(self.d):
            raise ParseError("unexpected EOF reading i32")
        v = struct.unpack_from("<i", self.d, self.o)[0]
        self.o += 4
        return v

    def f32(self) -> float:
        if self.o + 4 > len(self.d):
            raise ParseError("unexpected EOF reading f32")
        v = struct.unpack_from("<f", self.d, self.o)[0]
        self.o += 4
        return v

    def f64(self) -> float:
        if self.o + 8 > len(self.d):
            raise ParseError("unexpected EOF reading f64")
        v = struct.unpack_from("<d", self.d, self.o)[0]
        self.o += 8
        return v

    def varint(self) -> int:
        result = 0
        shift = 0
        while True:
            b = self.u8()
            result |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
            if shift >= 64:
                raise ParseError("varint too long")
        return result

    def varint64(self) -> int:
        return self.varint()

    def bytes(self, n: int) -> bytes:
        if self.o + n > len(self.d):
            raise ParseError("unexpected EOF reading bytes")
        v = self.d[self.o:self.o + n]
        self.o += n
        return v


@dataclass
class Const:
    tag: int
    value: object = None


@dataclass
class Proto:
    id: int
    maxstack: int = 0
    numparams: int = 0
    nups: int = 0
    is_vararg: int = 0
    flags: int = 0
    typeinfo: bytes = b""
    code: list = field(default_factory=list)
    consts: list = field(default_factory=list)
    protos: list = field(default_factory=list)
    linedefined: int = 0
    debugname: object = None
    linegaplog2: int = 0
    lineinfo_deltas: list = field(default_factory=list)
    lineinfo_baselines: list = field(default_factory=list)
    locvars: list = field(default_factory=list)
    upvals: list = field(default_factory=list)
    feedback: list = field(default_factory=list)
    cost: object = None


@dataclass
class Bytecode:
    version: int = 0
    typesversion: int = 0
    strings: list = field(default_factory=list)
    protos: list = field(default_factory=list)
    mainid: int = 0


def _strid(strings, sid):
    if sid == 0:
        return None
    if sid - 1 >= len(strings):
        raise ParseError(f"bad string id {sid}")
    return strings[sid - 1]


def decompose_import(iid: int):
    count = (iid >> 30) & 0x3
    id0 = (iid >> 20) & 1023 if count > 0 else -1
    id1 = (iid >> 10) & 1023 if count > 1 else -1
    id2 = (iid & 1023) if count > 2 else -1
    return (count, id0, id1, id2)


def parse(data: bytes) -> Bytecode:
    r = Reader(data)
    bc = Bytecode()

    bc.version = r.u8()
    if bc.version == 0:
        raise ParseError("error bytecode blob: " + data[1:101].decode("utf-8", "replace"))
    if not (LBC_VERSION_MIN <= bc.version <= LBC_VERSION_MAX):
        raise ParseError(f"expected v14, got {bc.version} (supported 3..14)")

    if bc.version >= 4:
        bc.typesversion = r.u8()

    # string table
    nstr = r.varint()
    for _ in range(nstr):
        ln = r.varint()
        bc.strings.append(r.bytes(ln))

    # userdata remapping (typesversion == 3)
    if bc.typesversion == 3:
        while True:
            idx = r.u8()
            if idx == 0:
                break
            _nameid = r.varint()  # string id, only mapping info

    nproto = r.varint()
    for pid in range(nproto):
        proto_start = r.o
        proto_size = 0
        if bc.version >= 12:
            proto_size = r.varint()
            proto_start = r.o

        p = Proto(id=pid)
        p.maxstack = r.u8()
        p.numparams = r.u8()
        p.nups = r.u8()
        p.is_vararg = r.u8()

        if bc.version >= 4:
            p.flags = r.u8()
            typesize = r.varint()
            p.typeinfo = r.bytes(typesize) if typesize else b""

        sizecode = r.varint()
        for _ in range(sizecode):
            p.code.append(r.u32())

        sizek = r.varint()
        for _ in range(sizek):
            tag = r.u8()
            if tag == LBC_CONSTANT_NIL:
                p.consts.append(Const(tag, None))
            elif tag == LBC_CONSTANT_BOOLEAN:
                p.consts.append(Const(tag, bool(r.u8())))
            elif tag == LBC_CONSTANT_NUMBER:
                p.consts.append(Const(tag, r.f64()))
            elif tag == LBC_CONSTANT_INTEGER:
                neg = r.u8()
                mag = r.varint64()
                v = -int(mag) if neg else int(mag)
                p.consts.append(Const(tag, v))
            elif tag == LBC_CONSTANT_VECTOR:
                p.consts.append(Const(tag, (r.f32(), r.f32(), r.f32(), r.f32())))
            elif tag == LBC_CONSTANT_VECTORD:
                p.consts.append(Const(tag, (r.f64(), r.f64(), r.f64(), r.f64())))
            elif tag == LBC_CONSTANT_STRING:
                sid = r.varint()
                p.consts.append(Const(tag, ("strid", sid, _strid(bc.strings, sid))))
            elif tag == LBC_CONSTANT_IMPORT:
                iid = r.u32()
                p.consts.append(Const(tag, ("import", iid, decompose_import(iid))))
            elif tag == LBC_CONSTANT_TABLE:
                nkeys = r.varint()
                keys = [r.varint() for _ in range(nkeys)]
                p.consts.append(Const(tag, ("table", keys)))
            elif tag == LBC_CONSTANT_TABLE_WITH_CONSTANTS:
                nkeys = r.varint()
                pairs = [(r.varint(), r.i32()) for _ in range(nkeys)]
                p.consts.append(Const(tag, ("tableconst", pairs)))
            elif tag == LBC_CONSTANT_CLOSURE:
                fid = r.varint()
                p.consts.append(Const(tag, ("closure", fid)))
            elif tag == LBC_CONSTANT_CLASS_SHAPE:
                cnid = r.varint()
                np = r.varint()
                nm = r.varint()
                mids = [r.varint() for _ in range(np + nm)]
                p.consts.append(Const(tag, ("classshape", cnid, np, nm, mids)))
            else:
                raise ParseError(f"unknown constant tag {tag} in proto {pid}")

        sizep = r.varint()
        for _ in range(sizep):
            p.protos.append(r.varint())

        p.linedefined = r.varint()
        dbgname = r.varint()
        p.debugname = _strid(bc.strings, dbgname)

        has_line = r.u8()
        if has_line:
            p.linegaplog2 = r.u8()
            last = 0
            for _ in range(len(p.code)):
                last = (last + r.u8()) & 0xFF
                p.lineinfo_deltas.append(last)
            intervals = ((len(p.code) - 1) >> p.linegaplog2) + 1 if p.code else 0
            lastline = 0
            for _ in range(intervals):
                lastline += r.i32()
                p.lineinfo_baselines.append(lastline)

        has_debug = r.u8()
        if has_debug:
            nloc = r.varint()
            for _ in range(nloc):
                nameid = r.varint()
                startpc = r.varint()
                endpc = r.varint()
                reg = r.u8()
                p.locvars.append({"name": _strid(bc.strings, nameid), "start": startpc, "end": endpc, "reg": reg})
            nup = r.varint()
            for _ in range(nup):
                nameid = r.varint()
                p.upvals.append(_strid(bc.strings, nameid))

        if bc.version >= 11:
            nfb = r.varint()
            for _ in range(nfb):
                kind = r.u8()
                if kind == 0:  # LFT_CALLTARGET-ish
                    p.feedback.append((kind, r.varint()))
                elif kind == 1:  # LFT_CONSTRUCT-ish
                    p.feedback.append((kind, r.varint()))
                else:
                    # be lenient: kinds are small ints, payload is varint
                    p.feedback.append((kind, r.varint()))

        if bc.version >= 12 and (p.flags & LPF_INLINABLE):
            p.cost = r.varint64()

        if bc.version >= 12:
            # skip unknown trailing data
            want = proto_start + proto_size
            if r.o > want:
                raise ParseError(f"proto {pid} overread by {r.o - want}")
            if r.o < want:
                r.o = want

        bc.protos.append(p)

    bc.mainid = r.varint()
    if bc.mainid >= len(bc.protos):
        raise ParseError(f"bad main id {bc.mainid}")
    if r.o != len(data):
        raise ParseError(f"trailing bytes: {len(data) - r.o}")
    return bc


def dump(bc: Bytecode) -> str:
    out = [f"version={bc.version} types={bc.typesversion} strings={len(bc.strings)} protos={len(bc.protos)} main={bc.mainid}"]
    for i, s in enumerate(bc.strings):
        try:
            txt = s.decode("utf-8")
        except Exception:
            txt = repr(s)
        out.append(f"  S{i + 1}={txt!r}")
    for p in bc.protos:
        out.append(f"Function {p.id} (name={p.debugname!r} line={p.linedefined}): maxstack={p.maxstack} params={p.numparams} nups={p.nups} vararg={p.is_vararg} flags={p.flags} code={len(p.code)} consts={len(p.consts)} protos={p.protos}")
        for j, ins in enumerate(p.code):
            out.append(f"    {j}: op={ins & 0xFF} raw=0x{ins:08x} (A={(ins >> 8) & 0xFF} B={(ins >> 16) & 0xFF} C={(ins >> 24) & 0xFF} D={(ins >> 16) & 0xFFFF})")
        for k, c in enumerate(p.consts):
            out.append(f"    K{k}: tag={c.tag} {c.value!r}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys, glob
    files = sys.argv[1:] or sorted(glob.glob("fixtures/v14/*.luac"))
    ok = True
    for f in files:
        try:
            bc = parse(open(f, "rb").read())
            print(f"OK {f}: {len(bc.protos)} protos, {sum(len(p.code) for p in bc.protos)} insns")
        except ParseError as e:
            ok = False
            print(f"FAIL {f}: {e}")
    if not ok:
        raise SystemExit(1)
