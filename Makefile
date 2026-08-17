PYTHON ?= python3
LLVM_CONFIG ?= $(shell command -v llvm-config 2>/dev/null)
ifneq ($(strip $(LLVM_CONFIG)),)
LLVM_HOME ?= $(patsubst %/bin/llvm-config,%,$(LLVM_CONFIG))
else
LLVM_HOME ?= Frontend/llvm-project/prefix
endif

.PHONY: all tools llvm-pass source-extractor test check clean-tools

all: tools

tools: llvm-pass source-extractor

llvm-pass:
	$(MAKE) -C Frontend/LLVM_PASS LLVM_HOME=$(abspath $(LLVM_HOME))

source-extractor:
	$(MAKE) -C Backend/AutoBackend/cpp LLVM_HOME=$(abspath $(LLVM_HOME))

test:
	PYTHONPYCACHEPREFIX=/tmp/linux-modular-pycache \
		$(PYTHON) -m unittest discover -v

check: tools test

clean-tools:
	$(MAKE) -C Frontend/LLVM_PASS clean LLVM_HOME=$(abspath $(LLVM_HOME))
	$(MAKE) -C Backend/AutoBackend/cpp clean LLVM_HOME=$(abspath $(LLVM_HOME))
