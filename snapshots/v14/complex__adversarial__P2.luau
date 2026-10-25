-- source: fixtures/v14/complex/adversarial.luac P2
R2 = false
count = 0
R4 = 0
R5 = 1
while true do
  R6 = #R0
  if R5 > R6 then
    break
  end
  R6 = R0[R5]
  R7 = 1
  start = 1
  R8 = #R0[R5]
  step = 1
  for i = start, R8, step do
    count += 1
    R11 = R6[i]
    if R11 == 0 then
      R4 = (R4 + 1) % 65535
      continue
    end
    R12 = 0
    if R11 < R12 then
      if R11 ~= -999 then
        R4 += R11
        break
      end
      R12 = false
      return false, count, -1
    end
    while R7 <= R11 do
      R7 += 2
      R12 = bit32.bxor(R4, R7)  -- fastcall
      R4 = R12
      R12 = 100000
      if R12 < R4 then
        break
      end
    end
    if R11 ~= R1 then
      continue
    end
    R2 = true
    break
  end
  if R2 then
    break
  end
  R8 = R4 % 2
  if R8 ~= 0 then
    R5 += 2
  else
    R5 += 1
  end
end
return R2, count, R4
