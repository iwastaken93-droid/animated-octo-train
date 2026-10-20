-- hunt for uncovered opcodes: force register-vs-register/const forms via function args
-- globals (GETGLOBAL / SETGLOBAL)
myGlobal = 123
print(myGlobal)

local function hunt(a, b, s, flag, x)
    -- JUMPIFEQ / JUMPIFNOTEQ (reg vs reg)
    if a == b then print("eq-reg") end
    if a ~= b then print("ne-reg") end

    -- DIVK, POWK, IDIVK (reg op const)
    local d1 = a / 2
    local d2 = a ^ 2
    local d3 = a // 4

    -- DIVRK (const / reg)
    local d4 = 100 / a

    -- AND / OR (reg and/or reg)
    local c1 = a and b
    local c2 = a or b

    -- ANDK (reg and const)
    local c3 = a and 5
    local c4 = a or 7

    -- NOT
    local f = not a

    -- JUMPXEQKNIL / JUMPXEQKB / JUMPXEQKS (reg vs const, non-foldable)
    if x == nil then print("is-nil") end
    if x ~= nil then print("not-nil") end
    if flag == true then print("is-true") end
    if flag ~= false then print("not-false") end
    if s == "hello" then print("is-hello") end
    if s ~= "bye" then print("not-bye") end

    -- FASTCALL3 (3 register args): string.sub(s, a, b), table.insert with 3 args
    local sub = string.sub(s, a, b)
    local t = { 1, 2, 3 }
    table.insert(t, a, b)

    -- CONCAT multi + LENGTH already covered, keep for shape
    return d1 + d2 + d3 + d4 + c1 + c2 + c3 + c4 + (f and 1 or 0) + #sub + #t
end

print(hunt(10, 20, "hello world", true, nil))
print(hunt(5, 5, "bye", false, 42))
return myGlobal
