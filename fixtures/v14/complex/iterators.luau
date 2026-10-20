-- generic for: pairs, ipairs, next, custom iterator, numeric edge cases
local t = { a = 1, b = 2, c = 3 }
local s1 = 0
for k, v in pairs(t) do
    s1 += v
end
print("pairs", s1)

local arr = { 10, 20, 30, 40 }
local s2 = 0
for i, v in ipairs(arr) do
    s2 += v * i
end
print("ipairs", s2)

local s3 = 0
for k, v in next, t do
    s3 += v
end
print("next", s3)

-- custom iterator (generic for with function state)
local function range(a, b, step)
    step = step or 1
    local i = a - step
    return function()
        i += step
        if (step > 0 and i <= b) or (step < 0 and i >= b) then
            return i
        end
        return nil
    end
end
local s4 = 0
for v in range(1, 10, 2) do
    s4 += v
end
print("custom", s4)

-- numeric for with negative step and float limits
local s5 = 0
for i = 10, 1, -2 do
    s5 += i
end
local s6 = 0.0
for i = 0.5, 2.5, 0.5 do
    s6 += i
end
print(s5, s6)

-- nested loops with break depth
local found = nil
for i = 1, 10 do
    for j = 1, 10 do
        if i * j == 42 then
            found = { i, j }
            break
        end
    end
    if found then
        break
    end
end
print(found and found[1], found and found[2])
return s1 + s2 + s3 + s4 + s5
