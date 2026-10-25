-- source: fixtures/v14/edge/struct_torture.luac P3
R1 = 0
while true do
  if R1 == 0 then
    R2 = 0
    if R0 <= R2 then
      break
    end
    R1 = 1
    continue
  end
  if R1 == 1 then
    R0 -= 1
    R2 = 0
    if R0 <= R2 then
      return 'drained'
    end
    R2 = R0 % 2
    if R2 ~= 0 then
      R1 = 2
    else
      R1 = 0
      continue
    end
    continue
  end
  R0 -= 2
  R2 = -10
  if R0 >= R2 then
    R1 = 1
    continue
  end
  str = tostring(R0)  -- fastcall
  R2 = 'broke:' .. str
  return 'broke:' .. str
end
return 'zero'
