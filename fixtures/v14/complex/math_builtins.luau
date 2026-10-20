-- arithmetic, idiv, comparisons (JUMPIFEQ family, JUMPXEQK*), fastcalls
local function arith(a, b)
    local r1 = a + b
    local r2 = a - b
    local r3 = a * b
    local r4 = a / b
    local r5 = a // b
    local r6 = a % b
    local r7 = a ^ b
    local r8 = -a
    local r9 = a + 10
    local r10 = 100 - a
    local r11 = a * 3
    local r12 = 50 - b
    return r1 + r2 + r3 + r4 + r5 + r6 + r7 + r8 + r9 + r10 + r11 + r12
end
print(arith(17, 5))

local x = 10
if x == 10 then print("eq-n") end
if x ~= 7 then print("ne-n") end
if x < 20 then print("lt-n") end
if x <= 10 then print("le-n") end

local str = "hello"
if str == "hello" then print("eq-s") end
if str ~= "bye" then print("ne-s") end

local n = nil
if n == nil then print("eq-nil") end
if n ~= nil then print("ne-nil-never") else print("ne-nil-else") end

local flag = true
if flag == true then print("eq-bool") end
if flag ~= false then print("ne-bool") end

print(math.abs(-5), math.floor(3.7), math.ceil(3.2), math.sqrt(16))
print(math.max(1, 9, 3), math.min(1, 9, 3), math.clamp(15, 1, 10))
print(string.len("hello"), type({}), type(42), tostring(123), tonumber("45"))
print(table.unpack({ 10, 20, 30 }), select(2, "a", "b", "c"))
print(bit32.band(12, 10), bit32.bor(12, 10))

local v = vector(1, 2, 3)
print(v.X, v.Y, v.Z, vector.magnitude(v))
return arith(3, 4)
