-- complex control flow: nested if, while, repeat, for, break, continue, and/or
local function classify(x, y)
    local r = ""
    if x > 10 and y < 5 then
        r = "a"
    elseif x == 0 or y == 0 then
        r = "b"
    else
        r = if x > y then "c" else "d"
    end
    return r
end

local sum = 0
for i = 1, 20, 2 do
    if i % 3 == 0 then
        continue
    end
    if i > 15 then
        break
    end
    sum += i
end

local j = 0
while j < 10 do
    j += 1
    if j % 2 == 0 then
        continue
    end
end

local k = 0
repeat
    k += 1
until k >= 5

local a = 1
local b = 2
local c = nil
local d = c and a or b
local e = not d
local f = #r .. tostring(sum)
print(classify(12, 3), classify(0, 9), sum, j, k, d, e, f)
return sum
