# Known limitations

This file records bytecode patterns the decompiler deliberately does not
translate into valid Luau, with the reason for each. The validity gate
(`valid_check.py`) counts these as `excluded`, not failures.

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
82 protos). Documented for completeness: if loop detection ever leaves
a join that is neither a loop exit nor a structured branch, the emitter
keeps `goto`/`L:` lines and marks the proto excluded rather than
emitting silently-wrong code. The `while true` + `if _pc == N` dispatcher
covers irreducible *non-FOR* flow; FOR loops containing irreducible flow
stay excluded because `for` init semantics cannot be re-entered.

## Proving a fix

Any new compilation for section 1 must (a) keep `valid_check.py` green,
(b) keep `struct_check_tmp.py` and `quality_check.py` green, and
(c) be verified by the execution-differential harness
(`diff_harness.py`): the decompiled proto must return identical values
to the original on the recorded inputs, including side-effect counts.
