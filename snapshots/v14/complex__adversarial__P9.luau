-- source: fixtures/v14/complex/adversarial.luac P9
R4..top = ...
R2, R3, R4 = R0('direct', ...)
tbl = {}  -- array 1
R6..top = R1(R2, R0(R3, R4))
tbl[1..] = ...multret...  -- setlist multret
R4 = {}  -- array 4
R11..top = math.min(#tbl, 3)  -- fastcall
R8 = table.unpack(...multret@9...)  -- fastcall
R9..top = R1('tail_marker')
R4[1..] = ...multret...  -- setlist multret
R6 = #R4
R7..top = table.unpack(R4)  -- fastcall
return R6..top
