-- source: fixtures/v14/complex/adversarial.luac P13
tbl = {}  -- array 0
total = 0
R3 = dupclosure 'customTriIterator'
pairsFn, pairsTbl, pairsKey = pairs(R0)
for k, v in pairsFn, pairsTbl, pairsKey do
  table.insert(tbl, k)  -- fastcall
  R9, R10, R11 = R3(v, 5)
  for k2, v2, R14 in R9, R10, R11 do
    R15 = 0
    if v2 <= R15 then
      continue
    end
    total += (R14 - k2)
    R15 = 50000
    if R15 < total then
      break
    else
      continue
    end
    break
  end
end
ipairsFn, ipairsTbl, ipairsKey = ipairs(tbl)
for k, v in ipairsFn, ipairsTbl, ipairsKey do
  R3 = #v
  R9 = #v % 2
  if R9 ~= 0 then
    R9 = k + R3
    total -= (k + R3)
  else
    R9 = k * #v
    total += R9
  end
end
return total, tbl
