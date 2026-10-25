-- source: fixtures/v14/complex/tables.luac P4
R0 = {1, 2, 3, 4, 5, 6, 7, 8}
R0.x = 10
R0.y = 20
R0.name = 'widget'
R0[1] = 100
R0[8] = 800
R1 = R0.x + R0.y
R0.z = R1
R1 = string.upper(R0.name)
R0.name = R1
print(R0[1], R0[8], R0.z, R0.name)
R1 = {}  -- array 0
R4 = 1
R2 = 64
R3 = 1
for i = R4, R2, R3 do
  R1[i] = i * i
end
R1.n = #R1
print(R1[1], R1[32], R1[64], R1.n)
R2, R3 = string.find('hello world foo', 'world')
R4 = string.sub('hello world foo', 2, 6)  -- fastcall
R5 = string.rep('ab', 3)
R6 = string.gsub('hello world foo', 'o', '0')
print(R2, R3, R4, R5, R6)
R6 = `player bob has {42} points (double={84})`
print(R6)
tbl = {pos = 0, items = 0, cb = 0}  -- dup K29
R7 = {x = 1, y = 2, z = 3}  -- dup K33
tbl.pos = R7
R7 = {'a', 'b', 'c'}
tbl.items = R7
tbl.cb = dupclosure 'cb'
R9 = tbl.pos.x
R8 = R9 + tbl.pos.y
R9 = tbl.items[2]
print(R8, R9, tbl:cb(21))
R7 = {}  -- array 0
R8 = {__index = 0, __newindex = 0, __len = 0}  -- dup K41
R8.__index = dupclosure '__index'
R8.__newindex = dupclosure '__newindex'
R8.__len = dupclosure '__len'
R6 = setmetatable(R7, R8)  -- fastcall
print(R6.anything, #R6)
R7 = R0.z + R1.n
return R7
