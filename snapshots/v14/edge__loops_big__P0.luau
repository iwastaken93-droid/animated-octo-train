-- source: fixtures/v14/edge/loops_big.luac P0
total = 0
start = 1
limit = 100
step = 1
for i = start, limit, step do
  start2 = 1
  limit2 = 50
  step2 = 1
  for j = start2, limit2, step2 do
    total += i * j
  end
end
return total
