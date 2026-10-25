-- source: fixtures/v14/complex/adversarial.luac P8
R5 = if not R0 then if not R2 then 0 else R3 + R4 else if not R1 then R4 * 3 else R3 * 2
if R1 then
  R6 = if R2 then R3 % R4 else R4 - R3
else
  R6 = if not R0 then R4 else if not R2 then R4 else R3
end
tbl = {primaryMode = 0, computedWeight = 0, flags = 0}  -- dup K5
R8 = if not R0 then if not R1 then 'OFFLINE' else 'STANDBY' else 'ACTIVE'
tbl.primaryMode = R8
R9 = 100
if R9 >= R5 then
  R9 = 50
  R8 = if R6 >= R9 then R5 + R6 else R6 * 2
else
  R8 = R5
end
tbl.computedWeight = R8
R8 = {canExecute = 0, fallback = 0, nestedPayload = 0}  -- dup K12
if not R0 then
  R9 = R2
  if R9 then
    R10 = 0
    R9 = R10 < R5
  end
else
  R9 = not R1
end
R8.canExecute = R9
if R1 then
  R9 = R6 == 0
else
  R9 = (not (not R2)) and (R6 == 0)
end
R8.fallback = R9
R9 = {score = 0}  -- dup K15
R10 = if not R2 then if not R1 then R6 else R5 else if not R0 then R4 else R3
R9.score = R10
R8.nestedPayload = R9
tbl.flags = R8
R9 = tbl.flags.canExecute
if R9 then
  return tbl
end
R8 = {primaryMode = 'DISABLED', computedWeight = -1}  -- dup K18
return R8
