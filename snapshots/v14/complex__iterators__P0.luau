-- source: fixtures/v14/complex/iterators.luac P0
U0 = U0 + U1
R0 = U1
R1 = 0
if R1 >= R0 then
  R0 = U1
  R1 = 0
  if R0 >= R1 then
    return nil
  end
  R0 = U0
  R1 = U2
  if R1 <= R0 then
    return U0
  end
  return nil
end
R0 = U0
R1 = U2
if R0 <= R1 then
  return U0
end
R0 = U1
R1 = 0
if R0 >= R1 then
  return nil
end
R0 = U0
R1 = U2
if R1 <= R0 then
  return U0
end
return nil
