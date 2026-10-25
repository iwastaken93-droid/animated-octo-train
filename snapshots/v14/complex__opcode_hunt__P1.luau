-- source: fixtures/v14/complex/opcode_hunt.luac P1
myGlobal = 123
print(myGlobal)
print(dupclosure 'hunt'(10, 20, 'hello world', true, nil))
print(dupclosure 'hunt'(5, 5, 'bye', false, 42))
return myGlobal
