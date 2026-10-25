-- source: fixtures/v14/complex/functions.luac P11
v = tostring(R0.v)  -- fastcall
R1 = 'Obj(' .. R0.name .. '=' .. v .. ')'
return R1
