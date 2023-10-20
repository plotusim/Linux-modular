LLVM_PATH := ../llvm-project/prefix

CXX := ../llvm-project/prefix/bin/clang++
CXXFLAGS := $(shell $(LLVM_PATH)/bin/llvm-config --cxxflags)
LDFLAGS := $(shell $(LLVM_PATH)/bin/llvm-config --ldflags)


TARGET1 := ExtendedFuncGraph.so
SRC1 := ExtendedFuncGraph.cpp


TARGET2 := Steensggard.so
SRC2 := Steensggard.cpp

.PHONY: all clean

all: $(TARGET1) $(TARGET2)

$(TARGET1): $(SRC1)
	$(CXX) $(CXXFLAGS) -Wl,-znodelete -fno-rtti -fPIC -shared $< -o $@ $(LDFLAGS)

$(TARGET2): $(SRC2)
	$(CXX) $(CXXFLAGS) -Wl,-znodelete -fno-rtti -fPIC -shared $< -o $@ $(LDFLAGS)

clean:
	rm -f $(TARGET1) $(TARGET2)
