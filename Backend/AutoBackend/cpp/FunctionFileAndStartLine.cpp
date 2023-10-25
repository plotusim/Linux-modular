#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <llvm/IR/DebugInfo.h>
#include <llvm/IR/DebugInfoMetadata.h>
#include <llvm/IR/Function.h>
#include <llvm/Bitcode/BitcodeReader.h>
#include <llvm/Support/raw_ostream.h>
#include <llvm/Support/SourceMgr.h>
#include <llvm/Support/MemoryBuffer.h>
#include <llvm/Support/Error.h>
#include "llvm/IRReader/IRReader.h"

#include <string>
#include <iostream>
#include <map>
#include <vector>
#include <regex>

using namespace llvm;

std::pair<std::string, int> getFunctionDebugInfo(const std::string &bitcodePath, const std::string &functionName) {
    LLVMContext context;
    SMDiagnostic error;
    std::unique_ptr<Module> module = parseIRFile(bitcodePath, error, context);

    if (!module) {
        std::cerr << "Error reading bitcode file.\n";
        return {};
    }

    for (auto &function : *module) {
        if (function.getName().str() == functionName) {
            if (auto *subprogram = function.getSubprogram()) {  // 获取与函数关联的元数据
                std::string filename = subprogram->getFilename().str();
                std::string directory = subprogram->getDirectory().str();
                int line = subprogram->getLine();
                return {directory + "/" + filename, line};  // 返回函数定义的文件名和行号
            }
        }
    }

    return {"", 0};  // 如果没有找到，返回空字符串和0
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " <bitcode_file> <function_name>" << std::endl;
        return 1;
    }

    std::string bitcodePath = argv[1];
    std::string functionName = argv[2];

    auto result = getFunctionDebugInfo(bitcodePath, functionName);
    auto file = std::get<0>(result);
    auto line = std::get<1>(result);


    if (line != 0) {
        std::cout << file << ":" << line << std::endl;
    } else {
        std::cerr << "Function not found or no debug info available." << std::endl;
        return 1;
    }

    return 0;
}