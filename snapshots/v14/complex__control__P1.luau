-- source: fixtures/v14/complex/control.luac P1
classify = dupclosure 'classify'
total = 0
R4 = 1
R2 = 20
R3 = 2
i = R4
for i = R4, R2, R3 do
  R5 = i % 3
  if R5 == 0 then
    continue
  end
  R5 = 15
  if R5 < i then
    break
  end
  total += i
end
R4 = i
count = 0
while true do
  R3 = 10
  if count >= R3 then
    break
  end
  count += 1
  R3 = count % 2
end
count2 = 0
while true do
  count2 += 1
  R4 = 5
  if R4 <= count2 then
    break
  end
end
R5 = #r
str = tostring(total)  -- fastcall
R4 = R5 .. str
R6 = classify(12, 3)
R7 = classify(0, 9)
print(R6, R7, total, count, count2, 2, false, R4)
return total
