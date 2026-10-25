-- source: fixtures/v14/loops.luac P0
start = 1
R0 = 10
R1 = 1
for i = start, R0, R1 do
  print(i)
end
count = 0
while true do
  R1 = 5
  if count >= R1 then
    break
  end
  count += 1
end
