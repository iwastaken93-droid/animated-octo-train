-- source: fixtures/v14/edge/struct_torture.luac P4
R7 = dupclosure 'dispatch'(2, 10)
R9 = {}  -- array 2
R9 = table.move({{1, 2}, {3}}, 1, 2, 1, R9)  -- setlist 2
R8 = dupclosure 'nest'(R9)
R6 = R7 + R8
tbl = {1, 2, 3}
R7 = dupclosure 'rep'(tbl)
R5 = R6 + R7
R7 = dupclosure 'machine'(5)
R4 = R5 + #R7
return R4
