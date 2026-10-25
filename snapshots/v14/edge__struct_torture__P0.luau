-- source: fixtures/v14/edge/struct_torture.luac P0
total = 0
R3 = true
while R3 do
  R3 = false
  if R0 ~= 0 then
    if R0 ~= 1 then
      if R0 ~= 2 then
        R4 = R1
        while true do
          R5 = 0
          if R5 >= R4 then
            break
          end
          R4 -= 1
          R5 = R4 % 7
          if R5 == 0 then
            break
          end
          total += R4
        end
      else
        start = 1
        R4 = R1
        R5 = 1
        for i = start, R4, R5 do
          R7 = i % 2
          if R7 == 0 then
            continue
          end
          R7 = 50
          if R7 < i then
            break
          end
          total += i
        end
      end
    else
      total -= R1
      R4 = 0
      if total < R4 then
        R0 = 0
        R3 = true
      end
    end
  else
    total += R1
  end
end
return total
