-- source: fixtures/v14/complex/adversarial.luac P11
U0 = U0 + 1
R0 = U0
R1 = U1
if R0 > R1 then
  return nil, nil, nil
end
R2 = U2[U0] * U0
return U0, U2[U0], R2
