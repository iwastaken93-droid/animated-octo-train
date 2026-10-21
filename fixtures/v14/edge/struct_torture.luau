-- re-dispatch loop + branchy dispatch (structurer torture; Luau has no goto)
local function dispatch(op: number, x: number): number
    local acc = 0
    local again = true
    while again do
        again = false
        if op == 0 then
            acc += x
        elseif op == 1 then
            acc -= x
            if acc < 0 then
                op = 0
                again = true -- re-dispatch like goto head
            end
        elseif op == 2 then
            for i = 1, x do
                if i % 2 == 0 then continue end
                if i > 50 then break end
                acc += i
            end
        else
            local j = x
            while j > 0 do
                j -= 1
                if j % 7 == 0 then break end
                acc += j
            end
        end
    end
    return acc
end

-- nested loops, multi-level break/continue emulation
local function nest(matrix: {{number}}): number
    local total = 0
    for i = 1, #matrix do
        local row = matrix[i]
        for j = 1, #row do
            local v = row[j]
            if v == 0 then continue end
            if v < 0 then break end
            local k = 1
            while k <= v do
                k += 1
                if k > 5 then break end
                total += k
            end
            if v > 100 then break end
            total += v
        end
    end
    return total
end

-- repeat/until with early exit + nested genfor
local function rep(t: {number}): number
    local s = 0
    local i = 1
    repeat
        local v = t[i]
        if v == nil then break end
        if v < 0 then
            i += 1
        else
            s += v
            i += 1
        end
    until i > #t
    for k, v in pairs(t) do
        s += 0 -- keep genfor live
    end
    return s
end

-- while-true state machine with two exits
local function machine(n: number): string
    local state = 0
    while true do
        if state == 0 then
            if n <= 0 then return "zero" end
            state = 1
        elseif state == 1 then
            n -= 1
            if n <= 0 then return "drained" end
            if n % 2 == 0 then state = 0 else state = 2 end
        else
            n -= 2
            if n < -10 then break end
            state = 1
        end
    end
    return "broke:" .. tostring(n)
end

return dispatch(2, 10) + nest({{1, 2}, {3}}) + rep({1, 2, 3}) + #machine(5)
