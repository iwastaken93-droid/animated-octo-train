-- source: fixtures/v14/complex/stress.luac P0
R0 = 55 + 155
R1 = 0
R2 = 100
R1 = if R2 >= R0 then R1 - 1 else 0 + 1
R2 = 150
R1 = if R2 >= R0 then R1 - 2 else R1 + 2
R2 = 200
R1 = if R2 >= R0 then R1 - 4 else R1 + 4
R2 = 250
R1 = if R2 >= R0 then R1 - 8 else R1 + 8
if R0 == 210 then
  R1 += 16
end
if R0 ~= 0 then
  R1 += 32
end
R2 = 210
if R2 <= R0 then
  R1 += 64
end
R2 = 210
if R0 <= R2 then
  R1 += 128
end
R2 = {}  -- array 0
R5 = 1
R3 = 200
R4 = 1
for i = R5, R3, R4 do
  R2[i] = i
end
total = 0
start = 1
R4 = #R2
R5 = 1
for j = start, R4, R5 do
  total += R2[j]
end
R2 = {}  -- array 7
tbl = {nested = 0}  -- dup K14
tbl.nested = {deep = 42} -- dup K17
R2 = table.move({1, 'two', 3.5, true, false, nil, tbl}, 1, 7, 1, R2)  -- setlist 7
R12 = R2[7].nested
R12 = R12.deep
print(R0, R1, total, 300, 'line1\nline2\ttab\\slash"quote"', R2[2], R2[4], R12)
R4 = R0 + R1 + total
return R4
