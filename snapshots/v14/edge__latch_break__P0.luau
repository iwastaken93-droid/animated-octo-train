-- source: fixtures/v14/edge/latch_break.luac P0
total = 0
R2 = true
while R2 do
  R2 = false
  start = 1
  limit = R0
  step = 1
  for i = start, limit, step do
    if i == 5 then
      break
    end
    total += i
  end
end
return total
