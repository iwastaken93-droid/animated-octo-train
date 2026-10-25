-- source: fixtures/v14/complex/adversarial.luac P3
R4..top = ...
R2 = select('#')  -- fastcall
tbl = {}  -- array 0
R4..top = ...
tbl[1..] = ...multret...  -- setlist multret
kind = type(R0)  -- fastcall
R7..top = ...
R5 = select('#')  -- fastcall
R4 = assert(R5, 'vararg query failed')  -- fastcall
clamped = math.clamp(R4, 0, 32)  -- fastcall
R4 = 0
R8 = 1
R6 = clamped
R7 = 1
for R8 = R8, R6, R7 do
  R9 = tbl[R8]
  R10 = type(tbl[R8])  -- fastcall
  if R10 ~= 'number' then
    R4 += 0.5
  else
    R12 = math.sin(R9)  -- fastcall
    R13 = math.rad(R1)  -- fastcall
    R10 = math.abs(R12 * R13)  -- fastcall
    R4 += R10
    R13 = 1
    R15..top = math.abs(R9)  -- fastcall
    R14..top = math.sqrt(...multret@15...)  -- fastcall
    R12..top = math.max(...multret@13...)  -- fastcall
    R10 = math.min(...multret@11...)  -- fastcall
    R4 = R10
  end
end
R6 = string.format('mode=%s;count=%d;accum=%.2f', R0, R2, R4)
R6 = R6:upper()  -- upper method
return R6, R4, clamped
