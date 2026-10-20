-- ============================================================================
-- LUAU DECOMPILER ADVERSARIAL STRESS TEST SUITE
-- Designed to target edge-cases in CFG structuring, live-range analysis,
-- SSA phi-node resolution, upvalue lifting, and Luau VM bytecode idioms.
-- ============================================================================

-- ============================================================================
-- TEST CASE 1: Deep Short-Circuit Boolean Tree vs. Branch Ambiguity
-- Targets: Distinguishing inline boolean short-circuit evaluation (and/or)
-- from control-flow branching (if/else), where condition jumps share exit targets.
-- ============================================================================

local function test_01_short_circuit_trees(a: any, b: any, c: any, d: any, e: any, f: any): any
    -- Luau optimizes chained logical operations into overlapping conditional jumps.
    -- Naive decompilers turn these into broken or nested if/else statements.
    local res1 = ((a and b) or (c and (d or e))) and (not f or (a and not c))
    local res2 = not a and (b or not c) and (d and e or not a) or (f and (not b or c))
    local res3 = (a == "init" or b == 0) and (c ~= nil and (d > 10 or e <= 5)) and not (f == false)

    -- Mixed inline boolean condition within an actual if/elseif/else tree
    if ((a or b) and not (c and (d or e))) or (res1 and not res2) then
        local intermediate = (res1 and res2) or (res3 and (a or c))
        if not intermediate or (d and not e) then
            return intermediate and res1 or (res2 and not res3)
        else
            return res3 or (a and not b)
        end
    elseif (res2 or not res3) and (a and (b or not d)) then
        return (res1 or res2) and (res3 or not a)
    else
        return not (res1 and res2) and (res3 or (a and not b))
    end
end

-- ============================================================================
-- TEST CASE 2: Aggressive Register Reuse Across Scopes (Live-Range Partitioning)
-- Targets: The Luau compiler reuses virtual register indices once an internal
-- scope ends. Decompilers must detect split live ranges to avoid merging
-- completely unrelated types into a single phantom variable.
-- ============================================================================

local function test_02_register_reuse_and_lifetimes(flag1: boolean, flag2: boolean, count: number): (string, number)
    local masterAccumulator = 0
    local logBuffer = ""

    do
        -- Scope A: Uses registers for string manipulation and formatting
        local tempStr = string.format("batch_init_%d", count)
        local hash = #tempStr * 31
        masterAccumulator += hash
        logBuffer ..= tempStr
    end

    -- Scope B: Reuses the exact same register slots that previously held tempStr and hash
    do
        local multiplier = count * 7
        local offset = 1024
        local intermediate = (multiplier - offset) // 3
        masterAccumulator = bit32.bxor(masterAccumulator, intermediate) + 5
    end

    if flag1 then
        -- Scope C1: Conditional branch reusing the register window for a table iteration
        local tempMap: { [string]: number } = { alpha = 10, beta = 20, gamma = 30 }
        for key, weight in pairs(tempMap) do
            masterAccumulator += (#key * weight)
        end
    else
        -- Scope C2: Alternate branch reusing the same register window for array math
        local rawVector = { 1.5, 2.5, 3.5, 4.5 }
        local scale = 2.0
        for i = 1, #rawVector do
            masterAccumulator += math.floor(rawVector[i] * scale)
        end
    end

    do
        -- Scope D: Final scope reusing the early registers again for output serialization
        local prefix = (flag2 and "VALID:") or "STALE:"
        local finalCode = (masterAccumulator % 997)
        logBuffer = prefix .. logBuffer .. ":" .. tostring(finalCode)
    end

    return logBuffer, masterAccumulator
end

-- ============================================================================
-- TEST CASE 3: Multi-Level Nested Loops, Break Targets, and Continue Semantics
-- Targets: CFG structuring without `goto`. Jump targets can overlap, making
-- it difficult to differentiate loop continues, outer breaks, and normal exits.
-- ============================================================================

local function test_03_nested_loops_and_breaks(matrix: { {number} }, target: number): (boolean, number, number)
    local found = false
    local totalEvaluated = 0
    local checksum = 0

    local i = 1
    while i <= #matrix do
        local row = matrix[i]
        local j = 1

        for colIdx = 1, #row do
            totalEvaluated += 1
            local cellValue = row[colIdx]

            if cellValue == 0 then
                -- Continue-like pattern for inner numeric for-loop
                checksum = (checksum + 1) % 65535
                continue
            end

            if cellValue < 0 then
                -- Early deep escape from entire operation
                if cellValue == -999 then
                    return false, totalEvaluated, -1
                end
                -- Conditional multi-step break
                checksum += cellValue
                break
            end

            while j <= cellValue do
                j += 2
                checksum = bit32.bxor(checksum, j)
                if checksum > 100000 then
                    break -- Breaks inner while, continues inner for
                end
            end

            if cellValue == target then
                found = true
                break -- Breaks inner for, hits outer check
            end
        end

        if found then
            -- Early exit from outer while loop
            break
        end

        if checksum % 2 == 0 then
            i += 1
        else
            i += 2
        end
    end

    return found, totalEvaluated, checksum
end

-- ============================================================================
-- TEST CASE 4: Fastcall Emits, Builtin Fallbacks, and Variadic Register Windows
-- Targets: Luau inserts FASTCALL opcodes directly before CALL opcodes.
-- Decompilers must fold the pair into a single function call rather than
-- duplicating nodes or misinterpreting dead bytecode paths.
-- ============================================================================

local function test_04_fastcalls_and_variadics(mode: string, baseMultiplier: number, ...: any): (string, number, number)
    local varargCount = select("#", ...)
    local varargTable = { ... }

    -- Targets FASTCALL1, FASTCALL2, and FASTCALL2K optimizations
    local typeCheck = type(mode) == "string"
    local rawLen = assert(select("#", ...), "vararg query failed")
    local clampedCount = math.clamp(rawLen, 0, 32)

    local accum = 0.0
    for idx = 1, clampedCount do
        local elem = varargTable[idx]
        if type(elem) == "number" then
            -- Multiple fastcall opportunities in a single arithmetic expression
            accum += math.abs(math.sin(elem) * math.rad(baseMultiplier))
            accum = math.min(accum, math.max(1.0, math.sqrt(math.abs(elem))))
        else
            accum += 0.5
        end
    end

    -- Test select() slicing and string method invocation (NAMECALL)
    local summaryStr = string.format("mode=%s;count=%d;accum=%.2f", mode, varargCount, accum)
    local upperSummary = summaryStr:upper()

    return upperSummary, accum, clampedCount
end

-- ============================================================================
-- TEST CASE 5: Upvalue Capture Topologies and Shared Mutability
-- Targets: Resolving CLOSURE + CAPTURE sequences where closures capture
-- variables across parent, sibling, and loop-local scopes.
-- ============================================================================

local function test_05_upvalue_topologies(seed: number)
    local sharedRoot = seed * 2
    local eventHooks = {}

    for loopSlot = 1, 4 do
        -- `loopSlot` is scoped to this iteration; `perIterationAccum` is distinct per closure.
        local perIterationAccum = loopSlot * 10

        local function innerWorker(delta: number): number
            -- Captures outer `sharedRoot`, loop-iteration `loopSlot`, and local `perIterationAccum`
            sharedRoot = (sharedRoot + delta) % 10000
            perIterationAccum += delta
            return sharedRoot + perIterationAccum + loopSlot
        end

        local function siblingQuery(): (number, number)
            -- Sibling closure capturing the same mutated `perIterationAccum`
            return perIterationAccum, sharedRoot
        end

        table.insert(eventHooks, {
            run = innerWorker,
            query = siblingQuery,
        })
    end

    local function supervisor(multiplier: number): number
        -- Captures `sharedRoot` across all child iterations
        sharedRoot *= multiplier
        local localSum = 0
        for _, handler in ipairs(eventHooks) do
            localSum += handler.run(multiplier)
        end
        return localSum + sharedRoot
    end

    return supervisor, eventHooks
end

-- ============================================================================
-- TEST CASE 6: Nested Ternary Idioms and Compound LValues
-- Targets: Luau `if a then b else c` expressions compiling down to jump patterns
-- identical to standard if-statements, often nested inside table construction.
-- ============================================================================

local function test_06_nested_ternaries_and_expressions(p1: boolean, p2: boolean, p3: boolean, valA: number, valB: number): { [string]: any }
    -- Inline nested conditional expressions
    local derived1 = if p1 then (if p2 then valA * 2 else valB * 3) else (if p3 then valA + valB else 0)
    local derived2 = if not p2 then (if p1 and p3 then valA else valB) else (if not p3 then valB - valA else valA % valB)

    -- Expressions directly embedded in table literal construction
    local config = {
        primaryMode = if p1 then "ACTIVE" else (if p2 then "STANDBY" else "OFFLINE"),
        computedWeight = if derived1 > 100 then derived1 else (if derived2 < 50 then derived2 * 2 else derived1 + derived2),
        flags = {
            canExecute = if p1 then not p2 else (p3 and derived1 > 0),
            fallback = if p2 or p3 then derived2 == 0 else false,
            nestedPayload = {
                score = if p3 then (if p1 then valA else valB) else (if p2 then derived1 else derived2)
            }
        }
    }

    -- Compound conditional return
    return if config.flags.canExecute then config else { primaryMode = "DISABLED", computedWeight = -1 }
end

-- ============================================================================
-- TEST CASE 7: Variable-Length Multi-Returns and Balanced Register Shifts
-- Targets: Multi-return register adjustments (CALL with C=0), unpack(),
-- and variadic forwarders that distort fixed register expectations.
-- ============================================================================

local function test_07_multireturn_and_register_windows(fnA: (...any) -> ...any, fnB: (...any) -> ...any, ...: any): (number, ...any)
    -- Multi-return captured into a localized slice
    local r1, r2, r3 = fnA("direct", ...)

    -- Function call directly consuming multi-returns from another call
    local combinedResult = { fnB(r1, fnA(r2, r3)) }

    -- Unpack in the middle of a table constructor vs. trailing unpack
    local dynamicTable = {
        "header",
        r1,
        table.unpack(combinedResult, 1, math.min(#combinedResult, 3)),
        fnB("tail_marker")
    }

    local finalCount = #dynamicTable
    -- Forwarding varargs with dynamic leading arguments
    return finalCount, table.unpack(dynamicTable)
end

-- ============================================================================
-- TEST CASE 8: Repeat...Until Semantics with Scoped Variables in Condition
-- Targets: `repeat ... until` loops where local declarations inside the body
-- remain visible to the trailing termination condition jump.
-- ============================================================================

local function test_08_repeat_until_scoping(initialSeed: number, threshold: number): (number, number)
    local totalCycles = 0
    local externalAccumulator = initialSeed

    repeat
        totalCycles += 1

        -- Variable declared INSIDE the repeat loop
        local loopStepDelta = (externalAccumulator * 3 + 7) % 31
        local stepIsValid = loopStepDelta > 2

        if stepIsValid then
            externalAccumulator += loopStepDelta
        else
            externalAccumulator += 1
        end

        local loopExitCriterion = (externalAccumulator >= threshold) and (loopStepDelta % 2 ~= 0)
        -- Scoping test: `loopStepDelta` and `loopExitCriterion` MUST be valid inside `until`
    until loopExitCriterion or (totalCycles > 100)

    -- Repeat loop nested inside a while loop with inverted conditions
    while externalAccumulator > 0 do
        local decrement = 5
        repeat
            externalAccumulator -= 1
            decrement -= 1
            local emergencyHalt = (externalAccumulator % 17 == 0)
        until decrement <= 0 or emergencyHalt

        if externalAccumulator % 2 == 0 then
            break
        end
    end

    return totalCycles, externalAccumulator
end

-- ============================================================================
-- TEST CASE 9: Generic For-Loops, State Iterators, and Generator Emulation
-- Targets: FORGPREP / FORGLOOP instruction emission and generator variable slots
-- (generator function, state, control variable, and iteration values).
-- ============================================================================

local function test_09_generic_for_and_iterators(dataset: { [string]: { number } }): (number, { string })
    local keysProcessed = {}
    local totalMetric = 0

    -- Custom stateful generator returning 3 variables
    local function customTriIterator(tbl: { number }, maxEntries: number)
        local idx = 0
        local len = math.min(#tbl, maxEntries)
        return function(): (number?, number?, number?)
            idx += 1
            if idx <= len then
                return idx, tbl[idx], tbl[idx] * idx
            end
            return nil, nil, nil
        end
    end

    -- Standard pairs iterator
    for key, values in pairs(dataset) do
        table.insert(keysProcessed, key)

        -- Nested custom generator consuming values from parent iteration
        for index, rawVal, computedVal in customTriIterator(values, 5) do
            if rawVal <= 0 then
                continue
            end

            totalMetric += (computedVal - index)
            if totalMetric > 50000 then
                break -- Break inner generic for
            end
        end
    end

    -- ipairs iterator with immediate register destruction inside loop
    for i, keyName in ipairs(keysProcessed) do
        local entryLen = #keyName
        if entryLen % 2 == 0 then
            totalMetric += (i * entryLen)
        else
            totalMetric -= (i + entryLen)
        end
    end

    return totalMetric, keysProcessed
end

-- ============================================================================
-- TEST CASE 10: State Machine Dispatch with Fallthrough Emulation (The Stress Boss)
-- Targets: Irreducible CFGs, computed dispatch tables, manual jump emulation,
-- and deeply interlaced conditional breaks and continues.
-- ============================================================================

local function test_10_state_machine_dispatch(inputTokens: { string }, initialSeed: number): (number, string)
    local state = 1 -- 1: IDLE, 2: READ, 3: PROCESS, 4: ERROR, 5: COMPLETE
    local pointer = 1
    local registerAcc = initialSeed
    local diagnosticLog = ""

    while true do
        if state == 1 then -- IDLE
            diagnosticLog ..= "[IDLE]"
            if pointer > #inputTokens then
                state = 5 -- Transition to COMPLETE
            else
                local tok = inputTokens[pointer]
                if tok == "START" then
                    state = 2 -- Transition to READ
                    pointer += 1
                elseif tok == "SKIP" then
                    pointer += 2
                    continue
                else
                    state = 4 -- Transition to ERROR
                end
            end

        elseif state == 2 then -- READ
            diagnosticLog ..= "[READ]"
            local tok = inputTokens[pointer]
            if not tok or tok == "END" then
                state = 5
            elseif tok == "RESET" then
                registerAcc = 0
                state = 1
                pointer += 1
            else
                local parsed = tonumber(tok)
                if parsed then
                    registerAcc = (registerAcc + parsed) % 2147483647
                    state = 3 -- Transition to PROCESS
                else
                    state = 4
                end
                pointer += 1
            end

        elseif state == 3 then -- PROCESS
            diagnosticLog ..= "[PROC]"
            -- Sub-branch with early exit check
            if registerAcc % 2 == 0 then
                registerAcc = registerAcc // 2
            else
                registerAcc = (registerAcc * 3 + 1) % 2147483647
            end

            if registerAcc == 1 then
                state = 5
            else
                state = 2 -- Loop back to READ
            end

        elseif state == 4 then -- ERROR
            diagnosticLog ..= "[ERR]"
            registerAcc = -1
            break -- Immediate termination on error state

        elseif state == 5 then -- COMPLETE
            diagnosticLog ..= "[DONE]"
            break

        else
            -- Unknown state fallback
            diagnosticLog ..= "[CORRUPT]"
            registerAcc = -9999
            break
        end

        -- Failsafe iteration cap
        if #diagnosticLog > 500 then
            diagnosticLog ..= "[TIMEOUT]"
            break
        end
    end

    return registerAcc, diagnosticLog
end

-- Export module runner
return {
    test_01_short_circuit_trees = test_01_short_circuit_trees,
    test_02_register_reuse_and_lifetimes = test_02_register_reuse_and_lifetimes,
    test_03_nested_loops_and_breaks = test_03_nested_loops_and_breaks,
    test_04_fastcalls_and_variadics = test_04_fastcalls_and_variadics,
    test_05_upvalue_topologies = test_05_upvalue_topologies,
    test_06_nested_ternaries_and_expressions = test_06_nested_ternaries_and_expressions,
    test_07_multireturn_and_register_windows = test_07_multireturn_and_register_windows,
    test_08_repeat_until_scoping = test_08_repeat_until_scoping,
    test_09_generic_for_and_iterators = test_09_generic_for_and_iterators,
    test_10_state_machine_dispatch = test_10_state_machine_dispatch,
}
