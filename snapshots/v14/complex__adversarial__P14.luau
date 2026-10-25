-- source: fixtures/v14/complex/adversarial.luac P14
R2 = 1
R3 = 1
R4 = R1
buf = ''
while true do
  if R2 ~= 1 then
    if R2 ~= 2 then
      if R2 ~= 3 then
        break
      end
      buf ..= '[PROC]'
      R6 = R4 % 2
      if R6 ~= 0 then
        R6 = R4 * 3 + 1
        R4 = R6 % 2147483647
      else
        R4 //= 2
      end
      R2 = if R4 ~= 1 then 2 else 5
    else
      R7 = '[READ]'
      buf ..= '[READ]'
      R6 = R0[R3]
      if not R6 then
        R2 = 5
      elseif R6 ~= 'END' then
        if R6 ~= 'RESET' then
          R7 = tonumber(R6)  -- fastcall
          if not R7 then
            R2 = 4
          else
            R4 = (R4 + R7) % 2147483647
            R2 = 3
          end
          R3 += 1
        else
          R4 = 0
          R2 = 1
          R3 += 1
        end
      else
        R2 = 5
      end
    end
  else
    R7 = '[IDLE]'
    buf ..= '[IDLE]'
    R6 = #R0
    if R6 >= R3 then
      R6 = R0[R3]
      if R6 ~= 'START' then
        if R6 == 'SKIP' then
          R3 += 2
          continue
        end
        R2 = 4
      else
        R2 = 2
        R3 += 1
      end
    else
      R2 = 5
    end
  end
  R6 = #buf
  R7 = 500
  if R7 >= R6 then
    continue
  end
  R7 = '[TIMEOUT]'
  buf ..= '[TIMEOUT]'
  return R4, buf
end
if R2 == 4 then
  buf ..= '[ERR]'
  R4 = -1
  return -1, buf
end
if R2 == 5 then
  buf ..= '[DONE]'
  return R4, buf
end
buf ..= '[CORRUPT]'
return -9999, buf
