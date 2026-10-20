-- tables: constructors, setlist, gettablen/settablen, namecall chains, interp strings
local t = { 1, 2, 3, 4, 5, 6, 7, 8, x = 10, y = 20, name = "widget" }
t[1] = 100
t[8] = 800
t.z = t.x + t.y
t.name = string.upper(t.name)
print(t[1], t[8], t.z, t.name)

local big = {}
for i = 1, 64 do
    big[i] = i * i
end
big.n = #big
print(big[1], big[32], big[64], big.n)

local s = "hello world foo"
local a, b = string.find(s, "world")
local sub = string.sub(s, 2, 6)
local rep = string.rep("ab", 3)
local gsub = string.gsub(s, "o", "0")
print(a, b, sub, rep, gsub)

local name = "bob"
local score = 42
local msg = `player {name} has {score} points (double={score * 2})`
print(msg)

local nested = {
    pos = { x = 1, y = 2, z = 3 },
    items = { "a", "b", "c" },
    cb = function(self, x) return x * 2 end,
}
print(nested.pos.x + nested.pos.y, nested.items[2], nested:cb(21))

local mt = {}
local prox = setmetatable({}, {
    __index = function(_, k) return "missing:" .. tostring(k) end,
    __newindex = function(_, k, v) print("set", k, v) end,
    __len = function(_) return 99 end,
})
print(prox.anything, #prox)
return t.z + big.n
