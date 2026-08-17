source_filename = "tests/fixtures/llvm/weak_default.ll"

declare ptr @weak_leaf(ptr)

define weak ptr @replaceable(ptr %value) {
entry:
  %result = call ptr @weak_leaf(ptr %value)
  ret ptr %result
}
