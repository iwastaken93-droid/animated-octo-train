-- source: fixtures/v14/complex/functions.luac P12
R0 = 0
R2 = P0(2)
R3 = P0(5)
R5 = R2.inc(3)
R6 = R2.get()
print(R5, R6, R3.inc(1))
print(dupclosure 'fact'(10))
R4, R5 = dupclosure 'sumAll'(1, 2, 3, 4, 5)
print(R4, R5)
R6, R7 = pcall(dupclosure(P5))  -- fastcall
print(R6, R7)
R8, R9 = xpcall(dupclosure(P7), dupclosure(P8))  -- fastcall
print(R8, R9)
R9 = {}  -- array 0
R9.__index = R9
R9.new = dupclosure 'new'
R9.add = dupclosure 'add'
R9.__tostring = dupclosure '__tostring'
R10 = R9.new('test')
R8 = R10
R10 = print
R11 = R8:add(7)  -- add method
R12..top = tostring(R8)  -- fastcall
R10(...multret@11...)
R8 = {1, 2, 3}
R6 = 0
R10 = R8
for v in R10 do
  R6 += 0
end
return R0 + R4
