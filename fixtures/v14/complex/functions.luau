-- closures, upvalues, recursion, varargs, pcall/xpcall, methods
local counter = 0
local function makeCounter(step)
    local n = 0
    local function inc(mult)
        n += step * (mult or 1)
        counter += 1
        return n
    end
    local function get()
        return n
    end
    return { inc = inc, get = get }
end

local c1 = makeCounter(2)
local c2 = makeCounter(5)
print(c1.inc(3), c1.get(), c2.inc(1))

local function fact(n)
    if n <= 1 then
        return 1
    end
    return n * fact(n - 1)
end
print(fact(10))

local function sumAll(...)
    local args = { ... }
    local s = 0
    for i, v in ipairs(args) do
        s += v
    end
    return s, #args
end
local s, n = sumAll(1, 2, 3, 4, 5)
print(s, n)

local ok, err = pcall(function()
    error("boom")
end)
print(ok, err)

local function risky(x)
    if x < 0 then
        error("negative!")
    end
    return math.sqrt(x)
end
local ok2, res2 = xpcall(function() return risky(-1) end, function(e) return "caught: " .. e end)
print(ok2, res2)

local Obj = {}
Obj.__index = Obj
function Obj.new(name)
    local self = setmetatable({ name = name, v = 0 }, Obj)
    return self
end
function Obj:add(x)
    self.v += x
    return self.v
end
function Obj:__tostring()
    return "Obj(" .. self.name .. "=" .. tostring(self.v) .. ")"
end
local o = Obj.new("test")
print(o:add(7), tostring(o))

local t = { 1, 2, 3 }
local total = 0
for v in t do
    total += 0
end
return counter + s
