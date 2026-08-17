source_filename = "tests/fixtures/llvm/available_externally.ll"

@sys_call_table = constant [1 x ptr] [ptr @table_syscall]

define available_externally i32 @header_helper(i32 %value) {
entry:
  %result = add i32 %value, 1
  ret i32 %result
}

define i32 @call_header_helper(i32 %value) {
entry:
  %result = call i32 @header_helper(i32 %value)
  ret i32 %result
}

define internal i32 @inline_hint_helper(i32 %value) #0 {
entry:
  %result = add i32 %value, 2
  ret i32 %result
}

define i64 @table_syscall(ptr %registers) {
entry:
  ret i64 0
}

define i64 @encoded_dispatch_base(i64 %a, i64 %b, i64 %c, i64 %d, i64 %e) {
entry:
  ret i64 0
}

define i64 @exercise_encoded_dispatch(i64 %offset) {
entry:
  %target = getelementptr i8, ptr @encoded_dispatch_base, i64 %offset
  %result = call i64 %target(i64 1, i64 2, i64 3, i64 4, i64 5)
  ret i64 %result
}

attributes #0 = { inlinehint }
