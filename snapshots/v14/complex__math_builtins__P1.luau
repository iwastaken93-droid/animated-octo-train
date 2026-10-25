-- source: fixtures/v14/complex/math_builtins.luac P1
print(dupclosure 'arith'(17, 5))
print('eq-n')
print('ne-n')
print('lt-n')
print('le-n')
print('eq-s')
print('ne-s')
print('eq-nil')
print('ne-nil-else')
print('eq-bool')
print('ne-bool')
R1 = print
abs = math.abs(-5)  -- fastcall
R3 = math.floor(37 / 10)  -- fastcall
R4 = math.ceil(16 / 5)  -- fastcall
R5..top = math.sqrt(16)  -- fastcall
R1(...multret@2...)
R1 = print
max = math.max(1, 9, 3)  -- fastcall
R3 = math.min(1, 9, 3)  -- fastcall
R4..top = math.clamp(15, 1, 10)  -- fastcall
R1(...multret@2...)
R1 = print
R2 = string.len('hello')  -- fastcall
R3 = type({})  -- fastcall
R4 = type(42)  -- fastcall
R5 = tostring(123)  -- fastcall
R6..top = tonumber('45')  -- fastcall
R1(...multret@2...)
R1 = print
Unpack = table.unpack({10, 20, 30})  -- fastcall
R1(Unpack, select(2, 'a', 'b', 'c'))
R1 = print
band = bit32.band(12, 10)  -- fastcall
R3..top = bit32.bor(12, 10)  -- fastcall
R1(...multret@2...)
R1 = vector(1, 2, 3)
R2 = print
R6..top = vector.magnitude(R1)  -- fastcall
R2(...multret@3...)
return R0(3, 4)
