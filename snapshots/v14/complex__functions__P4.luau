-- source: fixtures/v14/complex/functions.luac P4
tbl = {}  -- array 0
R1..top = ...
tbl[1..] = ...multret...  -- setlist multret
R1 = 0
ipairsFn, ipairsTbl, ipairsKey = ipairs(tbl)
for k, v in ipairsFn, ipairsTbl, ipairsKey do
  R1 += v
end
return R1, #tbl
