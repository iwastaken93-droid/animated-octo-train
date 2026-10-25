-- source: fixtures/v14/edge/expr_torture.luac P1
R3 = 0
if R3 >= R0 then
  R3 = 0
  if R1 >= R3 then
    return 3
  end
  return 4
end
R3 = 0
if R3 >= R1 then
  return 5
end
R3 = 0
if R3 >= R2 then
  return 6
end
return 7
