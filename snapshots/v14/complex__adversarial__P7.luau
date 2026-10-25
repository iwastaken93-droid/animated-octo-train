-- source: fixtures/v14/complex/adversarial.luac P7
tbl = {}  -- array 0
start = 1
limit = 4
step = 1
for i = start, limit, step do
  R11 = {run = 0, query = 0}  -- dup K4
  R11.run = P0
  R11.query = P1
  table.insert(tbl, R11)  -- fastcall
end
return P2, tbl
