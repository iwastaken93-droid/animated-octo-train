-- source: fixtures/v14/edge/latch_break.luac P2
tbl = {fused = 0, bypass = 0}  -- dup K4
tbl.fused = dupclosure 'fused'
tbl.bypass = dupclosure 'bypass'
return tbl
