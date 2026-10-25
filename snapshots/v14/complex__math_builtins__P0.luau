-- source: fixtures/v14/complex/math_builtins.luac P0
R24 = R0 + R1 + (R0 - R1)
R22 = (R24 + R0 * R1) + R0 / R1
R20 = (R22 + R0 // R1) + R0 % R1
R18 = (R20 + R0 ^ R1) + -R0
R17 = R18 + (R0 + 10)
R16 = R17 + (100 - R0)
R14 = (R16 + R0 * 3) + (50 - R1)
return R14
