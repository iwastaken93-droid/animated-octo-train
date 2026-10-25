-- source: fixtures/v14/edge/expr_torture.luac P0
local _pc = 0
while true do
  if _pc == 0 then
    if R0 then
      _pc = 2
    else
      _pc = 1
    end
  elseif _pc == 1 then
    if not R1 then
      _pc = 5
    else
      _pc = 2
    end
  elseif _pc == 2 then
    R5 = R2
    if not R5 then
      _pc = 4
    else
      _pc = 3
    end
  elseif _pc == 3 then
    R5 = R3 or R0
    _pc = 4
  elseif _pc == 4 then
    R4 = not R5
    if R4 then
      _pc = 7
    else
      _pc = 5
    end
  elseif _pc == 5 then
    R4 = R1
    if not R4 then
      _pc = 7
    else
      _pc = 6
    end
  elseif _pc == 6 then
    R4 = not R2
    _pc = 7
  elseif _pc == 7 then
    if R4 then
      _pc = 9
    else
      _pc = 8
    end
  elseif _pc == 8 then
    if not R0 then
      _pc = 16
    else
      _pc = 9
    end
  elseif _pc == 9 then
    if not R1 then
      _pc = 11
    else
      _pc = 10
    end
  elseif _pc == 10 then
    if not R2 then
      _pc = 16
    else
      _pc = 11
    end
  elseif _pc == 11 then
    if not R4 then
      _pc = 13
    else
      _pc = 12
    end
  elseif _pc == 12 then
    R5 = R0
    if R5 then
      _pc = 15
    else
      _pc = 13
    end
  elseif _pc == 13 then
    R5 = R1
    if not R5 then
      _pc = 15
    else
      _pc = 14
    end
  elseif _pc == 14 then
    R5 = not R3
    _pc = 15
  elseif _pc == 15 then
    return R5
  elseif _pc == 16 then
    if R0 == 0 then
      _pc = 18
    else
      _pc = 17
    end
  elseif _pc == 17 then
    if R1 ~= 1 then
      _pc = 21
    else
      _pc = 18
    end
  elseif _pc == 18 then
    if R2 == nil then
      _pc = 21
    else
      _pc = 19
    end
  elseif _pc == 19 then
    if R3 == false then
      _pc = 21
    else
      _pc = 20
    end
  elseif _pc == 20 then
    R5 = not (R4 and R3)
    return R5
  elseif _pc == 21 then
    return R4
  else
    break
  end
end
