-- source: fixtures/v14/complex/adversarial.luac P1
R5 = string.format('batch_init_%d', R2)
R6 = #R5 * 31
R3 = 0 + R6
R8 = R5
R4 = '' .. R5
R5 = R2 * 7
R7 = R2 * 7 - 1024
R6 = R7 // 3
R7 = bit32.bxor(R3, R7 // 3)  -- fastcall
R3 = R7 + 5
if not R0 then
  R5 = {1.5, 2.5, 3.5, 4.5}
  R9 = 4.5
  R8 = 1
  R6 = #R5
  R7 = 1
  for i = R8, R6, R7 do
    R10 = R5[i] * 2
    R9 = math.floor(R10)  -- fastcall
    R3 += R9
  end
else
  R5 = {alpha = 10, beta = 20, gamma = 30}  -- dup K19
  R6, R7, R8 = pairs(R5)
  for k, v in R6, R7, R8 do
    R3 += (#k * v)
  end
end
R5 = if not R1 then 'STALE:' else 'VALID:'
R7 = R5
R8 = R4
R9 = ':'
R10 = tostring(R3 % 997)  -- fastcall
R4 = R7 .. R8 .. R9 .. R10
return R4, R3
