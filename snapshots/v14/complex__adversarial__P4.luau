-- source: fixtures/v14/complex/adversarial.luac P4
R1 = (U0 + R0) % 10000
U0 = R1
U1 = U1 + R0
R1 = U0 + U1 + U2
return R1
