-- source: fixtures/v14/complex/control.luac P0
R3 = 10
if R3 >= R0 then
  if (R0 == 0) or (R1 == 0) then
    return 'b'
  end
  if R1 >= R0 then
    return 'd'
  end
  return 'c'
end
R3 = 5
if R1 < R3 then
  return 'a'
end
if (R0 == 0) or (R1 == 0) then
  return 'b'
end
if R1 >= R0 then
  return 'd'
end
return 'c'
