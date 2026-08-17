source_filename = "tests/fixtures/llvm/strong_override.ll"

declare ptr @strong_leaf(ptr)

define ptr @replaceable(ptr %value) {
entry:
  %result = call ptr @strong_leaf(ptr %value)
  ret ptr %result
}
