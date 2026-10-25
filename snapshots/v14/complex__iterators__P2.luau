-- source: fixtures/v14/complex/iterators.luac P2
R0 = {a = 1, b = 2, c = 3}  -- dup K6
total = 0
pairsFn, pairsTbl, pairsKey = pairs(R0)
for k2, v in pairsFn, pairsTbl, pairsKey do
  total += v
end
print('pairs', total)
R2 = {10, 20, 30, 40}
total2 = 0
ipairsFn, ipairsTbl, ipairsKey = ipairs(R2)
for k3, v2 in ipairsFn, ipairsTbl, ipairsKey do
  R9 = v2 * k3
  total2 += v2 * k3
end
print('ipairs', total2)
total3 = 0
R4 = next
R5 = R0
R6 = nil
for k3, v2 in R4, R5, R6 do
  total3 += v2
end
print('next', total3)
total4 = 0
R8 = 2
R5, R6, R7 = dupclosure 'range'(1, 10, 2)
for v2 in R5, R6, R7 do
  total4 += v2
end
print('custom', total4)
total5 = 0
R7 = 10
R5 = 1
R6 = -2
for i = R7, R5, R6 do
  total5 += i
end
total6 = 0
R8 = 0.5
R6 = 2.5
R7 = 0.5
for j = R8, R6, R7 do
  total6 += j
end
print(total5, total6)
R5 = nil
R8 = 1
R6 = 10
R7 = 1
for k = R8, R6, R7 do
  start = 1
  R9 = 10
  step = 1
  for i2 = start, R9, step do
    R12 = k * i2
    if R12 == 42 then
      R12 = {}  -- array 2
      R12 = table.move({k, i2}, 1, 2, 1, R12)  -- setlist 2
      R5 = R12
      break
    end
  end
  if R5 then
    break
  end
end
R6 = print
R7 = R5
if R7 then
  R7 = R5[1]
end
R8 = R5
if R8 then
  R8 = R5[2]
end
R6(R7, R8)
R8 = total + total2 + total3
R6 = R8 + total4 + total5
return R6
