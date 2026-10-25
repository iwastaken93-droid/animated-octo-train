-- source: fixtures/v14/edge/expr_torture.luac P2
total = 0
R3 = table.insert(R0, 99)  -- fastcall
if not R3 then
  R2 = 0
else
  R2 = 0
end
total += R2
table.sort(R0)
R2, R3 = table.unpack(R0)  -- fastcall
R5 = (R2 or 0) + (R3 or 0)
total += (R5 + #R0)
floor = math.floor(total / 2)  -- fastcall
R5 = total + floor
bxor = bit32.bxor(total, 13)  -- fastcall
return R5 + bxor
