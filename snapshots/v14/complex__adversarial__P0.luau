-- source: fixtures/v14/complex/adversarial.luac P0
local _pc = 0
while true do
  if _pc == 0 then
    if not R0 then
      _pc = 2
    else
      _pc = 1
    end
  elseif _pc == 1 then
    if R1 then
      _pc = 5
    else
      _pc = 2
    end
  elseif _pc == 2 then
    R6 = R2
    if not R6 then
      _pc = 8
    else
      _pc = 3
    end
  elseif _pc == 3 then
    if R3 then
      _pc = 5
    else
      _pc = 4
    end
  elseif _pc == 4 then
    R6 = R4
    if not R6 then
      _pc = 8
    else
      _pc = 5
    end
  elseif _pc == 5 then
    R6 = not R5
    if R6 then
      _pc = 8
    else
      _pc = 6
    end
  elseif _pc == 6 then
    R6 = R0
    if not R6 then
      _pc = 8
    else
      _pc = 7
    end
  elseif _pc == 7 then
    R6 = not R2
    _pc = 8
  elseif _pc == 8 then
    if R0 then
      _pc = 14
    else
      _pc = 9
    end
  elseif _pc == 9 then
    if R1 then
      _pc = 11
    else
      _pc = 10
    end
  elseif _pc == 10 then
    if R2 then
      _pc = 14
    else
      _pc = 11
    end
  elseif _pc == 11 then
    if not R3 then
      _pc = 13
    else
      _pc = 12
    end
  elseif _pc == 12 then
    R7 = R4
    if R7 then
      _pc = 16
    else
      _pc = 13
    end
  elseif _pc == 13 then
    R7 = not R0
    if R7 then
      _pc = 16
    else
      _pc = 14
    end
  elseif _pc == 14 then
    R7 = R5
    if not R7 then
      _pc = 16
    else
      _pc = 15
    end
  elseif _pc == 15 then
    R8 = not R1
    R7 = not R1 or R2
    _pc = 16
  elseif _pc == 16 then
    if R0 == 'init' then
      _pc = 18
    else
      _pc = 17
    end
  elseif _pc == 17 then
    R8 = false
    if R1 ~= 0 then
      _pc = 25
    else
      _pc = 18
    end
  elseif _pc == 18 then
    R8 = false
    if R2 == nil then
      _pc = 25
    else
      _pc = 19
    end
  elseif _pc == 19 then
    R9 = 10
    if R9 < R3 then
      _pc = 21
    else
      _pc = 20
    end
  elseif _pc == 20 then
    R8 = false
    R9 = 5
    if R4 > R9 then
      _pc = 25
    else
      _pc = 21
    end
  elseif _pc == 21 then
    if R5 == false then
      _pc = 23
    else
      _pc = 22
    end
  elseif _pc == 22 then
    R9 = false
    _pc = 24
  elseif _pc == 23 then
    R9 = true
    _pc = 24
  elseif _pc == 24 then
    R8 = not R9
    _pc = 25
  elseif _pc == 25 then
    if R0 then
      _pc = 27
    else
      _pc = 26
    end
  elseif _pc == 26 then
    if not R1 then
      _pc = 30
    else
      _pc = 27
    end
  elseif _pc == 27 then
    if not R2 then
      _pc = 32
    else
      _pc = 28
    end
  elseif _pc == 28 then
    if R3 then
      _pc = 30
    else
      _pc = 29
    end
  elseif _pc == 29 then
    if not R4 then
      _pc = 32
    else
      _pc = 30
    end
  elseif _pc == 30 then
    if not R6 then
      _pc = 48
    else
      _pc = 31
    end
  elseif _pc == 31 then
    if R7 then
      _pc = 48
    else
      _pc = 32
    end
  elseif _pc == 32 then
    if not R6 then
      _pc = 34
    else
      _pc = 33
    end
  elseif _pc == 33 then
    R9 = R7
    if R9 then
      _pc = 36
    else
      _pc = 34
    end
  elseif _pc == 34 then
    R9 = R8
    if not R9 then
      _pc = 36
    else
      _pc = 35
    end
  elseif _pc == 35 then
    R9 = R0 or R2
    _pc = 36
  elseif _pc == 36 then
    if not R9 then
      _pc = 39
    else
      _pc = 37
    end
  elseif _pc == 37 then
    if not R3 then
      _pc = 44
    else
      _pc = 38
    end
  elseif _pc == 38 then
    if R4 then
      _pc = 44
    else
      _pc = 39
    end
  elseif _pc == 39 then
    if not R9 then
      _pc = 41
    else
      _pc = 40
    end
  elseif _pc == 40 then
    R10 = R6
    if R10 then
      _pc = 43
    else
      _pc = 41
    end
  elseif _pc == 41 then
    R10 = R7
    if not R10 then
      _pc = 43
    else
      _pc = 42
    end
  elseif _pc == 42 then
    R10 = not R8
    _pc = 43
  elseif _pc == 43 then
    return R10
  elseif _pc == 44 then
    R10 = R8
    if R10 then
      _pc = 47
    else
      _pc = 45
    end
  elseif _pc == 45 then
    R10 = R0
    if not R10 then
      _pc = 47
    else
      _pc = 46
    end
  elseif _pc == 46 then
    R10 = not R1
    _pc = 47
  elseif _pc == 47 then
    return R10
  elseif _pc == 48 then
    if R7 then
      _pc = 50
    else
      _pc = 49
    end
  elseif _pc == 49 then
    if R8 then
      _pc = 58
    else
      _pc = 50
    end
  elseif _pc == 50 then
    if not R0 then
      _pc = 58
    else
      _pc = 51
    end
  elseif _pc == 51 then
    if R1 then
      _pc = 53
    else
      _pc = 52
    end
  elseif _pc == 52 then
    if R3 then
      _pc = 58
    else
      _pc = 53
    end
  elseif _pc == 53 then
    if R6 then
      _pc = 55
    else
      _pc = 54
    end
  elseif _pc == 54 then
    R9 = R7
    if not R9 then
      _pc = 57
    else
      _pc = 55
    end
  elseif _pc == 55 then
    R9 = R8
    if R9 then
      _pc = 57
    else
      _pc = 56
    end
  elseif _pc == 56 then
    R9 = not R0
    _pc = 57
  elseif _pc == 57 then
    return R9
  elseif _pc == 58 then
    R10 = R6 and R7
    R9 = not (R6 and R7)
    if not R9 then
      _pc = 62
    else
      _pc = 59
    end
  elseif _pc == 59 then
    R9 = R8
    if R9 then
      _pc = 62
    else
      _pc = 60
    end
  elseif _pc == 60 then
    R9 = R0
    if not R9 then
      _pc = 62
    else
      _pc = 61
    end
  elseif _pc == 61 then
    R9 = not R1
    _pc = 62
  elseif _pc == 62 then
    return R9
  else
    break
  end
end
