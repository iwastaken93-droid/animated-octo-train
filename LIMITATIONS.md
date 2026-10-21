# Known limitations

This file records bytecode patterns the decompiler deliberately does not
translate into valid Luau, with the reason for each, plus-differential
harness skips that are not decompiler failures. The validity gate
(`valid_check.py`) counts untranslatable protos as `excluded`, not
failures. Current corpus: 32 files, 85 protos, 0 `goto_edges`,
0 unclassified loops.

## 1. Multret `SETLIST` (dynamic-count table fill)

**Status:** excluded by design — 3 protos in the corpus
(`complex/adversarial.luac` P3 and P9, `complex/functions.luac` P4).

**Pattern:** a call with multret results (`CALL Rk .. -1`) followed by
`SETLIST Rt Rk -1 [batch]`: a runtime-determined number of values
(whatever is on the stack top) is appended to a table's array part.
Example disassembly:

```
CALL R2 -1 1
SETLIST R3 R4 -1 [1]
```

The emitter renders an honest placeholder, which the validity gate
rejects on purpose:

```
R3[1..] = ...multret...  -- setlist multret
```

**Why no valid form exists today:** Luau has no expression that
splices "registers Rk through stack-top" into a table fill. `{f()}`
captures one call's returns, but the `SETLIST` batch loop appends across
separate runtime value windows, and the count is only known at runtime.

**Possible future compilation** (unverified — needs the execution
harness first): materialize the window once, then move it:

```lua
local _tmp = {f()}                      -- capture the multret window
table.move(_tmp, 1, #_tmp, #t + 1, t)    -- append starting at batch
```

This is *not* emitted today because the batch-start index and the
interaction with prior batches have not been proven equivalent; see
"Proving a fix" below.

## 2. Goto/label leftovers (irreducible control flow)

**Status:** none in the current corpus (0 total `goto_edges` across all
85 protos). Documented for completeness: if loop detection ever leaves
a join that is neither a loop exit nor a structured branch, the emitter
keeps `goto`/`L:` lines and marks the proto excluded rather than
emitting silently-wrong code. The `while true` + `if _pc == N` dispatcher
covers irreducible *non-FOR* flow; FOR loops containing irreducible flow
stay excluded because `for` init semantics cannot be re-entered.

## 3. Harness skip: `iter_range` (cross-proto reference)

**Status:** skipped by the differential harness, not a decompiler
failure. The `iter_range` fixture references another proto (`P[0]`) that
has no standalone runtime form, so the harness cannot execute the
original for comparison. The proto itself structures and parses cleanly.
If the harness ever gains linked-proto execution, this skip should be
revisited.

## 4. Resolved issues (regression-pinned, not limitations)

These were real bugs, now fixed and covered so they stay fixed:

- **Outer-latch `break` vs `continue`:** a branch from an inner loop
  body directly to an outer loop's latch block must decompile to
  `break` (inner), not `continue` — `continue` would only skip one
  iteration and silently compute a wrong sum. Both compiler shapes are
  pinned by `fixtures/v14/edge/latch_break.luau`: *fused* (inner exit
  IS the latch block) and *bypass* (inner exit jumps to the latch
  block while the `for` sits in a non-final dispatch branch, so its
  end-label must jump over the `else` code). Guards are non-monotonic
  (`i == 5`, `i == 7`) so a `continue`/`break` mixup changes the
  numeric result and the differential harness catches it.
- **`DUPTABLE` template emission:** template constants render as table
  literals (`disassembler_v14._fmt_duptable`, inlined by `ir_v14`);
  covered by the `iter_main` differential test.

## Proving a fix

Any new compilation for section 1 must (a) keep `valid_check.py` green,
(b) keep `struct_check_tmp.py` and `quality_check.py` green, and
(c) be verified by the execution-differential harness
(`diff_harness.py`): the decompiled proto must return identical values
to the original on the recorded inputs, including side-effect counts.
