-- short-circuit boolean trees vs real branches (expression-lifting torture)
local function bools(a: any, b: any, c: any, d: any): any
    local x = ((a or b) and not (c and (d or a))) or (b and not c)
    if (x or a) and (not b or c) then
        return (x and a) or (b and not d)
    elseif (a == 0 or b == 1) and (c ~= nil and d ~= false) then
        return not (x and d)
    else
        return x
    end
end

local function nested_tern(a: number, b: number, c: number): number
    if a > 0 then
        if b > 0 then
            if c > 0 then return 7 else return 6 end
        else
            return 5
        end
    else
        if b < 0 then return 4 else return 3 end
    end
end

-- fastcall-heavy + multireturn windows
local function fcs(t: {number}): number
    local s = 0
    s += table.insert(t, 99) and 0 or 0
    table.sort(t)
    local a, b = table.unpack(t)
    s += (a or 0) + (b or 0) + #t
    return s + math.floor(s / 2) + bit32.bxor(s, 13)
end

return nested_tern(1, 2, 3) + fcs({3, 1, 2}) + #(bools(true, false, nil, 1) and "y" or "n")
