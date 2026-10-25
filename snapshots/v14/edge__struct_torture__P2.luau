-- source: fixtures/v14/edge/struct_torture.luac P2
total = 0
R2 = 1
while true do
  R3 = R0[R2]
  if R3 == nil then
    break
  end
  R4 = 0
  if R3 >= R4 then
    total += R3
    R2 += 1
  else
    R2 += 1
  end
  R4 = #R0
  if R4 < R2 then
    break
  end
end
pairsFn, pairsTbl, pairsKey = pairs(R0)
for k, v in pairsFn, pairsTbl, pairsKey do
  total += 0
end
return total
