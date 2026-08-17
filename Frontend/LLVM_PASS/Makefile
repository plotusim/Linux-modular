LLVM_HOME ?= ../llvm-project/prefix
LLVM_CONFIG ?= $(LLVM_HOME)/bin/llvm-config
CXX = $(LLVM_HOME)/bin/clang++
CXXFLAGS := $(shell $(LLVM_CONFIG) --cxxflags)
# Pass plugins must use the LLVM symbols exported by their host (opt/clang).
# Linking the component archives into the plugin creates a second copy of
# LLVM's global command-line registry and makes opt abort while loading it.
PLUGIN_LDFLAGS := $(shell $(LLVM_CONFIG) --ldflags --system-libs)


TARGET1 := ExtendedFuncGraph.so
SRC1 := ExtendedFuncGraph.cpp


TARGET2 := Steensggard.so
SRC2 := Steensggard.cpp

TARGET3 := ReferenceFacts.so
SRC3 := ReferenceFacts.cpp

.PHONY: all legacy clean

# ReferenceFacts is the production schema-v1 pass.  The historical DOT and
# Steensgaard passes are kept behind an explicit target.
all: $(TARGET3)

legacy: $(TARGET1) $(TARGET2)

$(TARGET1): $(SRC1)
	$(CXX) $(CXXFLAGS) -Wl,-znodelete -fno-rtti -fPIC -shared $< -o $@ $(PLUGIN_LDFLAGS)

$(TARGET2): $(SRC2)
	$(CXX) $(CXXFLAGS) -Wl,-znodelete -fno-rtti -fPIC -shared $< -o $@ $(PLUGIN_LDFLAGS)

$(TARGET3): $(SRC3)
	$(CXX) $(CXXFLAGS) -Wl,-znodelete -fno-rtti -fPIC -shared $< -o $@ $(PLUGIN_LDFLAGS)

clean:
	rm -f $(TARGET1) $(TARGET2) $(TARGET3)
