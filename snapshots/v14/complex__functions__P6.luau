-- source: fixtures/v14/complex/functions.luac P6
R1 = 0
if R0 < R1 then
  error('negative!')
end
R1..top = math.sqrt(R0)  -- fastcall
return R1..top
