-- source: fixtures/v14/complex/opcode_hunt.luac P0
if R0 == R1 then
  print('eq-reg')
end
if R0 ~= R1 then
  print('ne-reg')
end
if R4 == nil then
  print('is-nil')
end
if R4 ~= nil then
  print('not-nil')
end
if R3 == true then
  print('is-true')
end
if R3 ~= false then
  print('not-false')
end
if R2 == 'hello' then
  print('is-hello')
end
if R2 ~= 'bye' then
  print('not-bye')
end
R14 = string.sub(R2, R0, R1)  -- fastcall
tbl = {1, 2, 3}
table.insert(tbl, R0, R1)  -- fastcall
R23 = ((R0 / 2) + (R0 ^ 2) + R0 // 4) + 100 / R0
R22 = R23 + (R0 and R1)
R21 = R22 + (R0 or R1)
R20 = R21 + (R0 and 5)
R19 = R20 + (R0 or 7)
R20 = if not (not R0) then 0 else 1
R17 = (R19 + R20) + #R14
R16 = R17 + #tbl
return R16
