LLVM_PATH := /path/to/llvm

CXX := clang++
CXXFLAGS := $(shell $(LLVM_PATH)/bin/llvm-config --cxxflags)
LDFLAGS := $(shell $(LLVM_PATH)/bin/llvm-config --ldflags)

TARGET := ExtendedCallGraph.so
SRC := ExtendedCallGraph.cpp

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) -Wl,-znodelete -fno-rtti -fPIC -shared $< -o $@ $(LDFLAGS)

clean:
	rm -f $(TARGET)
