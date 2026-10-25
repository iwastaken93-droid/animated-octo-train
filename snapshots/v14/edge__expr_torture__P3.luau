-- source: fixtures/v14/edge/expr_torture.luac P3
R5 = dupclosure 'nested_tern'(1, 2, 3)
tbl = {3, 1, 2}
R6 = dupclosure 'fcs'(tbl)
R4 = R5 + R6
R7 = dupclosure 'bools'(true, false, nil, 1)
R6 = if not R7 then 'n' else 'y'
R3 = R4 + #R6
return R3
