-- source: fixtures/v14/complex/adversarial.luac P10
count = 0
R3 = R0
while true do
  count += 1
  R5 = R3 * 3 + 7
  R4 = R5 % 31
  R6 = 2
  R5 = R6 < R4
  R3 = if not R5 then R3 + 1 else R3 + R4
  R5 = false
  if R1 <= R3 then
    R6 = R4 % 2
    R5 = R6 ~= 0
  end
  if R5 then
    break
  end
  R6 = 100
  if R6 < count then
    break
  else
    continue
  end
  break
end
while true do
  R4 = 0
  if R4 >= R3 then
    break
  end
  R4 = 5
  while true do
    R3 -= 1
    R4 -= 1
    R6 = R3 % 17
    R5 = R6 == 0
    R6 = 0
    if R4 <= R6 then
      break
    end
    if R5 then
      break
    else
      continue
    end
    break
  end
  R5 = R3 % 2
  if R5 == 0 then
    break
  end
end
return count, R3
