-- source: fixtures/v14/edge/latch_break.luac P1
total = 0
R3 = true
while R3 do
  R3 = false
  if R1 ~= 0 then
    if R1 ~= 2 then
      R4 = R0
      while true do
        R5 = 0
        if R5 >= R4 then
          break
        end
        R4 -= 1
        total += R4
      end
    else
      start = 1
      R4 = R0
      R5 = 1
      for i = start, R4, R5 do
        R7 = i % 2
        if R7 == 0 then
          continue
        end
        if i == 7 then
          break
        end
        total += i
      end
    end
  else
    total += R0
  end
end
return total
