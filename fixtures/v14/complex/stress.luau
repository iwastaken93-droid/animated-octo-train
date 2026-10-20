-- stress: many locals, long chain of branches, big table, long string
local v001, v002, v003, v004, v005 = 1, 2, 3, 4, 5
local v006, v007, v008, v009, v010 = 6, 7, 8, 9, 10
local v011, v012, v013, v014, v015 = 11, 12, 13, 14, 15
local v016, v017, v018, v019, v020 = 16, 17, 18, 19, 20

local acc = v001 + v002 + v003 + v004 + v005 + v006 + v007 + v008 + v009 + v010
acc += v011 + v012 + v013 + v014 + v015 + v016 + v017 + v018 + v019 + v020

local branch = 0
if acc > 100 then branch += 1 else branch -= 1 end
if acc > 150 then branch += 2 else branch -= 2 end
if acc > 200 then branch += 4 else branch -= 4 end
if acc > 250 then branch += 8 else branch -= 8 end
if acc == 210 then branch += 16 end
if acc ~= 0 then branch += 32 end
if acc >= 210 then branch += 64 end
if acc <= 210 then branch += 128 end

local big = {}
for i = 1, 200 do
    big[i] = i
end
local total = 0
for i = 1, #big do
    total += big[i]
end

local longstr = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    .. "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    .. "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"

local esc = "line1\nline2\ttab\\slash\"quote\""

local mixed = { 1, "two", 3.5, true, false, nil, { nested = { deep = 42 } } }
print(acc, branch, total, #longstr, esc, mixed[2], mixed[4], mixed[7].nested.deep)
return acc + branch + total
