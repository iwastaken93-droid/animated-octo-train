-- latch regression: a branch from an inner loop body directly to an outer
-- loop's latch block must decompile to `break` (inner), not `continue`.
-- Guards are non-monotonic (`i == 5`, `i == 7`): `continue` would skip only
-- that iteration (wrong sum), `break` ends the loop. Covers both compiler
-- shapes: fused (inner exit IS the latch block: the for is the whole body)
-- and bypass (inner exit jumps to the latch block: the for sits in a
-- non-final dispatch branch, so its endLabel must jump over the else code).
local function fused(x)
  local acc = 0
  local again = true
  while again do
    again = false
    for i = 1, x do
      if i == 5 then break end
      acc += i
    end
  end
  return acc
end

local function bypass(x, mode)
  local acc = 0
  local again = true
  while again do
    again = false
    if mode == 0 then
      acc += x
    elseif mode == 2 then
      for i = 1, x do
        if i % 2 == 0 then continue end
        if i == 7 then break end
        acc += i
      end
    else
      local j = x
      while j > 0 do
        j -= 1
        acc += j
      end
    end
  end
  return acc
end

return { fused = fused, bypass = bypass }
