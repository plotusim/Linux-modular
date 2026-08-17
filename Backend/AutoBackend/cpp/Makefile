LLVM_HOME ?= ../../../Frontend/llvm-project/prefix
LLVM_CONFIG ?= $(LLVM_HOME)/bin/llvm-config
CXX = $(LLVM_HOME)/bin/clang++
LLVM_INCLUDE := $(shell $(LLVM_CONFIG) --includedir)
LLVM_LIB := $(shell $(LLVM_CONFIG) --libdir)
LLVM_CXXFLAGS := $(shell $(LLVM_CONFIG) --cxxflags)
LLVM_LDFLAGS := $(shell $(LLVM_CONFIG) --ldflags --system-libs --libs)
CXXFLAGS = $(LLVM_CXXFLAGS) -std=c++17 -fno-exceptions -fno-rtti
LDFLAGS = $(LLVM_LDFLAGS)
CLANG_CPP = $(firstword $(wildcard $(LLVM_LIB)/libclang-cpp.so*))


TARGET1 := ExtractFuncSym
SRC1 := ExtractFuncSym.cpp

TARGET2 := ExtractGlobalVar
SRC2 := ExtractGlobalVar.cpp

TARGET3 := GlobalVariableLocation
SRC3 := GlobalVariableLocation.cpp

TARGET4 := FunctionFileAndStartLine
SRC4 := FunctionFileAndStartLine.cpp

TARGET5 := SourceExtractor
SRC5 := SourceExtractor.cpp

.PHONY: all legacy clean

# The schema-v1 backend uses SourceExtractor.  The four older one-purpose
# tools depend on pre-opaque-pointer LLVM APIs and remain available through
# the explicit `legacy` target for historical LLVM 15 environments.
all: $(TARGET5)

legacy: $(TARGET1) $(TARGET2) $(TARGET3) $(TARGET4)

$(TARGET1): $(SRC1)
	$(CXX) $(CXXFLAGS) $(COMMON_FLAGS) $< -o $@ $(LDFLAGS) $(LIBS)

$(TARGET2): $(SRC2)
	$(CXX) $(CXXFLAGS) $(COMMON_FLAGS) $< -o $@ $(LDFLAGS) $(LIBS)

$(TARGET3): $(SRC3)
	$(CXX) $(CXXFLAGS) $(COMMON_FLAGS) $< -o $@ $(LDFLAGS) $(LIBS)

$(TARGET4): $(SRC4)
	$(CXX) $(CXXFLAGS) $(COMMON_FLAGS) $< -o $@ $(LDFLAGS) $(LIBS)

$(TARGET5): $(SRC5)
	@test -n "$(CLANG_CPP)" || (echo "libclang-cpp not found in $(LLVM_LIB)" && false)
	$(CXX) $(LLVM_CXXFLAGS) -std=c++17 $< -o $@ \
		$(CLANG_CPP) -Wl,-rpath,$(LLVM_LIB) \
		$(LLVM_LDFLAGS)

clean:
	rm -f $(TARGET1) $(TARGET2) $(TARGET3) $(TARGET4) $(TARGET5)
