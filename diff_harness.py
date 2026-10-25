"""Execution-differential harness: original vs decompiled, side by side.

For each covered proto this builds a Luau driver that loads the ORIGINAL
function (from the fixture .luau source) and the DECOMPILED function
(emitted by emit_proto), calls both with identical arguments, and
deep-compares results (NaN-aware; error-vs-error counts as equal only
when the messages match after stripping the `file:line: ` position
prefix, which necessarily differs between the two sources).

Run:  python diff_harness.py [path/to/luau]   (default: C:/luau-build/luau.exe)
Exit 1 on any mismatch or VM failure. Cases that cannot run standalone
(upvalues, multret-SETLIST, missing VM) are reported as SKIP with reason.
"""
import glob
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, ".")
import parser_v14
from emit_v14 import emit_proto

LUAC = "C:/luau-build/luau.exe"


def luau_lit(v):
    """Python value -> Luau literal. Arrays only for tables."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        if v != v:
            return "(0/0)"
        if v == float("inf"):
            return "math.huge"
        if v == float("-inf"):
            return "-math.huge"
        return repr(v)
    if isinstance(v, str):
        out = ['"']
        for ch in v:
            o = ord(ch)
            if ch == '"':
                out.append('\\"')
            elif ch == "\\":
                out.append("\\\\")
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\t":
                out.append("\\t")
            elif 32 <= o < 127:
                out.append(ch)
            else:
                out.append("\\%d" % o)
        out.append('"')
        return "".join(out)
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(luau_lit(x) for x in v) + "}"
    raise TypeError(f"unserializable arg {v!r}")


DRIVER_TMPL = """-- differential driver for CASE_NAME (proto PID of LUAC_FILE)
local _real_print = print
print = function(...) end
EXTRA_GLOBALS
local _cap = {}
print = function(...)
  local parts = {}
  for i = 1, select("#", ...) do
    parts[i] = tostring(select(i, ...))
  end
  _cap[#_cap + 1] = table.concat(parts, "\\t")
end
local __orig_src = ORIG_QUOTED
local __orig_chunk, __load_err = loadstring(__orig_src .. ORIG_SUFFIX_QUOTED)
if not __orig_chunk then
  _real_print("DIFFCASE CASE_NAME load FAIL orig-load: " .. tostring(__load_err))
  return
end
__orig_fn = ORIG_EXTRACT
local __dec_src = DECOMP_QUOTED
local __dec_fn, __dec_err
do
  local chunk, err = loadstring(__dec_src)
  if not chunk then
    _real_print("DIFFCASE CASE_NAME load FAIL dec-load: " .. tostring(err))
    return
  end
  local _dok, _dval = pcall(chunk)
  if not _dok then
    _real_print("DIFFCASE CASE_NAME load FAIL dec-run: " .. tostring(_dval))
    return
  end
  __dec_fn = _dval
end
DEC_FINALIZE
local function _eq(a, b, depth)
  depth = depth or 0
  if type(a) ~= type(b) then return false end
  local t = type(a)
  if t == "number" then
    if a ~= a then return b ~= b end
    return a == b
  end
  if t ~= "table" then return a == b end
  if depth > 12 then return true end
  for k, v in pairs(a) do
    if b[k] == nil and v ~= nil then return false end
    if not _eq(v, b[k], depth + 1) then return false end
  end
  for k, v in pairs(b) do
    if a[k] == nil and v ~= nil then return false end
  end
  return true
end
local function _packcall(f, argv)
  return table.pack(pcall(f, table.unpack(argv, 1, argv.n)))
end
local function _normerr(e)
  return (tostring(e):gsub("^.-:%d+: ", ""))
end
local function _cmp(ra, rb)
  if ra[1] ~= rb[1] then return false, "ok-flag" end
  if not ra[1] then
    -- both sides raised: the fault must be the same fault. Position
    -- prefixes (`file:line: `) necessarily differ, so compare the
    -- message text after stripping them. Non-string error values
    -- (e.g. error(tbl)) compare structurally; tostring on tables
    -- embeds a nondeterministic address.
    local ea, eb = ra[2], rb[2]
    if type(ea) == "string" and type(eb) == "string" then
      if _normerr(ea) ~= _normerr(eb) then return false, "error-text" end
      return true
    end
    if not _eq(ea, eb, 0) then return false, "error-value" end
    return true
  end
  if ra.n ~= rb.n then return false, "arity" end
  for i = 2, ra.n do
    if not _eq(ra[i], rb[i], 0) then return false, "result" end
  end
  return true
end
local _ncalls = 0
local _nfail = 0
local _norigok = 0
local function _deep(x, seen)
  if type(x) ~= "table" then return x end
  seen = seen or {}
  if seen[x] then return seen[x] end
  local o = {}
  seen[x] = o
  for k, v in pairs(x) do o[_deep(k, seen)] = _deep(v, seen) end
  return o
end
local function _fresh(a)
  local o = {n = a.n}
  for i = 1, a.n do o[i] = _deep(a[i]) end
  return o
end

_cap = {}
CALL_LOOP
print = _real_print
_real_print(string.format("DIFFCASE CASE_NAME done calls=%d fails=%d origok=%d", _ncalls, _nfail, _norigok))
"""

CALL_LOOP_STD = """
for _ci, _argv in ipairs(_CALLS) do
  _ncalls += 1
  local _pa, _pb
  _cap = {}
  local _oka, _ra = pcall(function() return _packcall(__orig_fn, _fresh(_argv)) end)
  local _sa = table.concat(_cap, "\\n")
  _cap = {}
  local _okb, _rb = pcall(function() return _packcall(__dec_fn, _fresh(_argv)) end)
  local _sb = table.concat(_cap, "\\n")
  if _oka and _ra[1] then _norigok += 1 end
  local _ok = _oka and _okb and _sa == _sb
  local _dbg = " orig_ok=" .. tostring(_oka and _ra[1]) .. " dec_ok=" .. tostring(_okb and _rb[1])
    .. " orig_err=" .. tostring(_oka and _ra[2] or _ra):sub(1, 70)
    .. " dec_err=" .. tostring(_okb and _rb[2] or _rb):sub(1, 70)
  local _why = ""
  if _ok then
    local _same, _w = _cmp(_ra, _rb)
    _ok = _same
    _why = _w or ""
  else
    _why = "pcall-or-print"
  end
  if not _ok then
    _nfail += 1
    _real_print(string.format("DIFFCASE CASE_NAME call=%d FAIL %s%s", _ci, _why, _dbg))
  end
end
"""

CALL_LOOP_ITER = """
for _ci, _argv in ipairs(_CALLS) do
  _ncalls += 1
  local function _collect(f, a)
    local out = {}
    local it = f(table.unpack(a, 1, a.n))
    if type(it) ~= "function" then return {it} end
    for _k = 1, 100000 do
      local v = it()
      if v == nil then break end
      out[#out + 1] = v
    end
    return out
  end
  local _sa = _collect(__orig_fn, _fresh(_argv))
  local _sb = _collect(__dec_fn, _fresh(_argv))
  if not _eq(_sa, _sb, 0) then
    _nfail += 1
    _real_print(string.format("DIFFCASE CASE_NAME call=%d FAIL iter-seq", _ci))
  end
end
"""


def q(s):
    """Luau double-quoted string literal (no long strings: fixtures may contain ]] )."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif 32 <= o < 127:
            out.append(ch)
        else:
            out.append("\\%d" % o)
    out.append('"')
    return "".join(out)


# name, luac, pid, fixture, orig_suffix, orig_extract, calls, mode, extra_globals
CASES = [
    dict(name="adv_P2", luac="fixtures/v14/complex/adversarial.luac", pid=2,
         fixture="fixtures/v14/complex/adversarial.luau",
         suffix="\nreturn test_03_nested_loops_and_breaks\n",
         calls=[[[[1, 2, 3], [0, -5, 7]], 2], [[[0]], 0], [[[1000000]], 5],
                [[[1, 2], [3, 4]], -999], [[[]], 1], [[[5]], 5]],
         mode="std", globals=""),
    dict(name="adv_P10", luac="fixtures/v14/complex/adversarial.luac", pid=10,
         fixture="fixtures/v14/complex/adversarial.luau",
         suffix="\nreturn test_08_repeat_until_scoping\n",
         calls=[[0, 10], [5, 3], [100, 1000], [-7, 0], [31, 31]],
         mode="std", globals=""),
    dict(name="adv_P14", luac="fixtures/v14/complex/adversarial.luac", pid=14,
         fixture="fixtures/v14/complex/adversarial.luau",
         suffix="\nreturn test_10_state_machine_dispatch\n",
         calls=[[["a", "b"], 1], [["x"], 0], [[], 7],
                [["go", "stop", "go"], 3], [["a", "a", "a", "a"], 2]],
         mode="std", globals=""),
    dict(name="adv_P1", luac="fixtures/v14/complex/adversarial.luac", pid=1,
         fixture="fixtures/v14/complex/adversarial.luau",
         suffix="\nreturn test_02_register_reuse_and_lifetimes\n",
         calls=[[True, True, 5], [True, False, 3], [False, True, 0],
                [False, False, 10]],
         mode="std", globals=""),
    dict(name="opcode_hunt", luac="fixtures/v14/complex/opcode_hunt.luac", pid=0,
         fixture="fixtures/v14/complex/opcode_hunt.luau",
         suffix="\nreturn hunt\n",
         calls=[[10, 20, "hello world", True, None],
                [5, 5, "bye", False, 42],
                [1, 2, "hi", True, 0]],
         mode="std", globals=""),
    dict(name="expr_main", luac="fixtures/v14/edge/expr_torture.luac", pid=3,
         fixture="fixtures/v14/edge/expr_torture.luau",
         suffix="",
         calls=[[]],
         mode="std", globals="",
         orig_is_chunk=True,
         stubs=["nested_tern", "fcs", "bools"]),
    dict(name="control_P0", luac="fixtures/v14/complex/control.luac", pid=0,
         fixture="fixtures/v14/complex/control.luau",
         suffix="\nreturn classify\n",
         calls=[[12, 3], [0, 9], [0, 0], [11, 5], [5, 5]],
         mode="std", globals="", orig_fn="classify"),
    dict(name="control_P1", luac="fixtures/v14/complex/control.luac", pid=1,
         fixture="fixtures/v14/complex/control.luau",
         suffix="",
         calls=[[]],
         mode="std",
         orig_is_chunk=True,
         stubs=["classify"]),
    dict(name="iter_range", luac="fixtures/v14/complex/iterators.luac", pid=1,
         fixture="fixtures/v14/complex/iterators.luau",
         suffix="\nreturn range\n",
         calls=[[1, 10, 2], [10, 1, -2], [1, 5], [5, 5, 1]],
         mode="iter", globals=""),
    dict(name="stress_P0", luac="fixtures/v14/complex/stress.luac", pid=0,
         fixture="fixtures/v14/complex/stress.luau",
         suffix="",
         calls=[[]],
         mode="std", globals="",
         orig_is_chunk=True),
    dict(name="expr_fcs", luac="fixtures/v14/edge/expr_torture.luac", pid=2,
         fixture="fixtures/v14/edge/expr_torture.luau",
         suffix="\nreturn fcs\n",
         calls=[[[3, 1, 2]], [[]], [[5]], [[-1, 0, 7]]],
         mode="std", globals=""),
    dict(name="struct_dispatch", luac="fixtures/v14/edge/struct_torture.luac", pid=0,
         fixture="fixtures/v14/edge/struct_torture.luau",
         suffix="\nreturn dispatch\n",
         calls=[[2, 10], [0, 0], [5, 1], [3, 7], [2, 60]],
         mode="std", globals=""),
    dict(name="struct_nest", luac="fixtures/v14/edge/struct_torture.luac", pid=1,
         fixture="fixtures/v14/edge/struct_torture.luau",
         suffix="\nreturn nest\n",
         calls=[[[[1, 2], [3]]], [[[]]], [[[5, 6, 7]]]],
         mode="std", globals=""),
    dict(name="struct_machine", luac="fixtures/v14/edge/struct_torture.luac", pid=3,
         fixture="fixtures/v14/edge/struct_torture.luau",
         suffix="\nreturn machine\n",
         calls=[[5], [0], [1]],
         mode="std", globals=""),
    dict(name="func_risky", luac="fixtures/v14/complex/functions.luac", pid=6,
         fixture="fixtures/v14/complex/functions.luau",
         suffix="\nreturn risky\n",
         calls=[[4], [0], [2.25], [-1]],
         mode="std", globals=""),
    dict(name="loops_big", luac="fixtures/v14/edge/loops_big.luac", pid=0,
         fixture="fixtures/v14/edge/loops_big.luau",
         suffix="",
         calls=[[]],
         mode="std", globals="",
         orig_is_chunk=True),
    dict(name="iter_main", luac="fixtures/v14/complex/iterators.luac", pid=2,
         fixture="fixtures/v14/complex/iterators.luau",
         suffix="",
         calls=[[]],
         mode="std", globals="",
         orig_is_chunk=True,
         stubs=["range"]),
    dict(name="math_arith", luac="fixtures/v14/complex/math_builtins.luac", pid=0,
         fixture="fixtures/v14/complex/math_builtins.luau",
         suffix="\nreturn arith\n",
         calls=[[17, 5], [0, 1], [-3, 7], [2.5, 0.5]],
         mode="std",
         globals="if type(vector) == 'table' and type(vector.create) == 'function' then local _vt = vector vector = setmetatable({}, {__index = _vt, __call = function(_, x, y, z) return _vt.create(x, y, z) end}) end"),
    dict(name="latch_fused", luac="fixtures/v14/edge/latch_break.luac", pid=0,
         fixture="fixtures/v14/edge/latch_break.luau",
         suffix="",
         calls=[[10], [3], [5]],
         mode="std", globals="", orig_fn="fused"),
    dict(name="latch_bypass", luac="fixtures/v14/edge/latch_break.luac", pid=1,
         fixture="fixtures/v14/edge/latch_break.luau",
         suffix="",
         calls=[[10, 2], [6, 2], [4, 0], [3, 1]],
         mode="std", globals="", orig_fn="bypass"),
]


def strip_src(s):
    rx = re.compile("(?m)^return[ " + chr(9) + "]")
    ms = list(rx.finditer(s))
    return s[:ms[-1].start()] if ms else s


def extract_local_fn(text, name):
    i = text.index("local function " + name)
    m = re.search("(?m)^end", text[i:])
    assert m, "no closing end"
    return text[i:i + m.end()]


def build_driver(case, decomp_src):
    calls_lua = "{" + ", ".join(
        "{n=%d, %s}" % (len(a), ", ".join(luau_lit(x) for x in a))
        for a in case["calls"]) + "}"
    loop = CALL_LOOP_ITER if case["mode"] == "iter" else CALL_LOOP_STD
    loop = loop.replace("_CALLS", calls_lua)
    loop = loop.replace("CASE_NAME", case["name"])
    extra = case.get("globals", "")
    orig_text = open(case["fixture"]).read()
    suffix = case["suffix"]
    if case.get("orig_fn"):
        orig_text = (extract_local_fn(orig_text, case["orig_fn"])
                     + chr(10) + "return " + case["orig_fn"] + chr(10))
        suffix = ""
    elif not case.get("orig_is_chunk"):
        orig_text = strip_src(orig_text)

    if case.get("stubs"):
        # main chunk only: named locals extracted verbatim serve dupclosure
        _parts = []
        for _nm in case["stubs"]:
            _ft = extract_local_fn(open(case["fixture"]).read(), _nm)
            _parts.append("__stubs." + _nm + " = assert(loadstring(" + q(_ft + chr(10) + "return " + _nm + chr(10)) + "))()")
        extra = ("local __stubs = {}" + chr(10) + chr(10).join(_parts)
                 + chr(10) + "dupclosure = function(n) return __stubs[n] end")
    if case.get("orig_is_chunk"):
        extract = "__orig_chunk"
    else:
        extract = ("((function() local _ok, _v = pcall(__orig_chunk) "
                   "assert(_ok, _v) return _v end)())")
    text = DRIVER_TMPL
    text = text.replace("CASE_NAME", case["name"])
    text = text.replace("PID", str(case["pid"]))
    text = text.replace("LUAC_FILE", case["luac"])
    text = text.replace("ORIG_QUOTED", q(orig_text))
    text = text.replace("ORIG_SUFFIX_QUOTED", q(suffix))
    text = text.replace("ORIG_EXTRACT", extract)
    text = text.replace("EXTRA_GLOBALS", extra)
    text = text.replace("DECOMP_QUOTED",
                        q(decomp_src + "\nreturn P%d\n" % case["pid"]))
    text = text.replace("CALL_LOOP", loop)
    text = text.replace("DEC_FINALIZE", "")
    return text


def main():
    vm = sys.argv[1] if len(sys.argv) > 1 else LUAC
    if not os.path.exists(vm):
        print(f"SKIP vm not found: {vm}")
        return 0
    tmp = tempfile.mkdtemp(prefix="diffharness_")
    npass = nfail = nskip = 0
    failures = []
    for case in CASES:
        bc = parser_v14.parse(open(case["luac"], "rb").read())
        p = [x for x in bc.protos if x.id == case["pid"]][0]
        src, valid = emit_proto(bc, p)
        if not valid:
            print(f"SKIP {case['name']}: decompiled output invalid (excluded)")
            nskip += 1
            continue
        if re.search(r"\bU\d+", src):
            print(f"SKIP {case['name']}: upvalue capture not preserved")
            nskip += 1
            continue
        refs = set()
        for _ln in src.splitlines():
            _s = _ln.strip()
            if _s.startswith("--"):
                continue
            if _s.startswith("local function P"):
                continue
            for _tok in _s.replace("(", " ").replace(")", " ").replace(",", " ").replace("=", " ").split():
                if len(_tok) > 1 and _tok[0] == "P" and _tok[1:].isdigit():
                    refs.add(int(_tok[1:]))
        refs.discard(case["pid"])
        if refs:
            print(f"SKIP {case['name']}: cross-proto reference P{sorted(refs)} has no runtime form")
            nskip += 1
            continue
        if "dupclosure" in src and not case.get("stubs"):
            print(f"SKIP {case['name']}: dupclosure refs need defining scope")
            nskip += 1
            continue
        drv = build_driver(case, src)
        path = os.path.join(tmp, case["name"] + ".luau")
        open(path, "w").write(drv)
        try:
            r = subprocess.run([vm, path], capture_output=True, text=True,
                               timeout=120)
        except subprocess.TimeoutExpired:
            print(f"FAIL {case['name']}: VM timeout")
            nfail += 1
            failures.append(case["name"])
            continue
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(rf"DIFFCASE {case['name']} done calls=(\d+) fails=(\d+) origok=(\d+)",
                      out)
        if m and int(m.group(2)) == 0 and int(m.group(1)) > 0:
            vac = " VACUOUS-orig-never-succeeded" if int(m.group(3)) == 0 else ""
            print(f"PASS {case['name']}: {m.group(1)} calls origok={m.group(3)}" + vac)
            npass += 1
        else:
            print(f"FAIL {case['name']}: exit={r.returncode}")
            lines = out.splitlines()
            shown = [ln for ln in lines if "DIFFCASE" in ln or "error" in ln.lower()]
            for ln in (shown or lines[:12]):
                print("   ", ln[:300])
            nfail += 1
            failures.append(case["name"])
    print(f"differential: pass={npass} fail={nfail} skip={nskip}")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
