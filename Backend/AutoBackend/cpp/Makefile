LLVM_HOME = ../../../Frontend/llvm-project/prefix
LLVM_INCLUDE = $(LLVM_HOME)/include
LLVM_LIB = $(LLVM_HOME)/lib
CXX = $(LLVM_HOME)/bin/clang++
CXXFLAGS = -I$(LLVM_INCLUDE) -std=c++14 -fno-exceptions -fno-rtti -D_GNU_SOURCE -D__STDC_CONSTANT_MACROS -D__STDC_FORMAT_MACROS -D__STDC_LIMIT_MACROS -Wl,-znodelete
LDFLAGS = -L$(LLVM_LIB) -lLLVMIRReader -lLLVMBitReader -lLLVMAsmParser -lLLVMCore -lLLVMRemarks -lLLVMBitstreamReader -lLLVMBinaryFormat -lLLVMSupport -lLLVMDemangle -ltinfo


TARGET1 := ExtractFuncSym
SRC1 := ExtractFuncSym.cpp

TARGET2 := ExtractGlobalVar
SRC2 := ExtractGlobalVar.cpp

TARGET3 := GlobalVariableLocation
SRC3 := GlobalVariableLocation.cpp

TARGET4 := FunctionFileAndStartLine
SRC4 := FunctionFileAndStartLine.cpp

.PHONY: all clean

all: $(TARGET1) $(TARGET2) $(TARGET3)  $(TARGET4)

$(TARGET1): $(SRC1)
	$(CXX) $(CXXFLAGS) $(COMMON_FLAGS) $< -o $@ $(LDFLAGS) $(LIBS)

$(TARGET2): $(SRC2)
	$(CXX) $(CXXFLAGS) $(COMMON_FLAGS) $< -o $@ $(LDFLAGS) $(LIBS)

$(TARGET3): $(SRC3)
	$(CXX) $(CXXFLAGS) $(COMMON_FLAGS) $< -o $@ $(LDFLAGS) $(LIBS)

$(TARGET4): $(SRC4)
	$(CXX) $(CXXFLAGS) $(COMMON_FLAGS) $< -o $@ $(LDFLAGS) $(LIBS)

clean:
	rm -f $(TARGET1) $(TARGET2)
