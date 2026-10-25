-- source: fixtures/v14/edge/struct_torture.luac P1
total = 0
start = 1
limit = #R0
step = 1
for i = start, limit, step do
  R5 = R0[i]
  start2 = 1
  limit2 = #R0[i]
  step2 = 1
  for j = start2, limit2, step2 do
    R9 = R5[j]
    if R9 ~= 0 then
      R10 = 0
      if R9 < R10 then
        break
      else
        R10 = 1
        while R10 <= R9 do
          R10 += 1
          R11 = 5
          if R11 < R10 then
            break
          end
          total += R10
        end
        R11 = 100
        if R11 < R9 then
          break
        end
        total += R9
        continue
      end
      break
    end
  end
end
return total
