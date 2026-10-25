-- source: fixtures/v14/complex/adversarial.luac P15
tbl = {test_01_short_circuit_trees = 0, test_02_register_reuse_and_lifetimes = 0, test_03_nested_loops_and_breaks = 0, test_04_fastcalls_and_variadics = 0, test_05_upvalue_topologies = 0, test_06_nested_ternaries_and_expressions = 0, test_07_multireturn_and_register_windows = 0, test_08_repeat_until_scoping = 0, test_09_generic_for_and_iterators = 0, test_10_state_machine_dispatch = 0}  -- dup K20
tbl.test_01_short_circuit_trees = dupclosure 'test_01_short_circuit_trees'
tbl.test_02_register_reuse_and_lifetimes = dupclosure 'test_02_register_reuse_and_lifetimes'
tbl.test_03_nested_loops_and_breaks = dupclosure 'test_03_nested_loops_and_breaks'
tbl.test_04_fastcalls_and_variadics = dupclosure 'test_04_fastcalls_and_variadics'
tbl.test_05_upvalue_topologies = dupclosure 'test_05_upvalue_topologies'
tbl.test_06_nested_ternaries_and_expressions = dupclosure 'test_06_nested_ternaries_and_expressions'
tbl.test_07_multireturn_and_register_windows = dupclosure 'test_07_multireturn_and_register_windows'
tbl.test_08_repeat_until_scoping = dupclosure 'test_08_repeat_until_scoping'
tbl.test_09_generic_for_and_iterators = dupclosure 'test_09_generic_for_and_iterators'
tbl.test_10_state_machine_dispatch = dupclosure 'test_10_state_machine_dispatch'
return tbl
