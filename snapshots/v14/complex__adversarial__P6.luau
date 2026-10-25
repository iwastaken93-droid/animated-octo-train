-- source: fixtures/v14/complex/adversarial.luac P6
U0 = U0 * R0
total = 0
ipairsFn, ipairsTbl, ipairsKey = ipairs(U1)
for k, v in ipairsFn, ipairsTbl, ipairsKey do
  R7 = v.run(R0)
  total += R7
end
return total + U0
