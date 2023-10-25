#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <llvm/IR/GlobalVariable.h>
#include <llvm/IR/DebugInfoMetadata.h>
#include <llvm/Support/SourceMgr.h>
#include <llvm/Support/Error.h>
#include <llvm/IRReader/IRReader.h>

#include <iostream>
#include <string>

int main(int argc, char **argv) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <IR file>" << std::endl;
        return 1;
    }

    llvm::LLVMContext context;
    llvm::SMDiagnostic error;
    auto module = llvm::parseIRFile(argv[1], error, context);

    if (!module) {
        error.print(argv[0], llvm::errs());
        return 1;
    }

    for (auto &global: module->globals()) {
        // 根据新的API，我们需要一个容器来保存结果
        llvm::SmallVector<llvm::DIGlobalVariableExpression *, 1> GVs;
        global.getDebugInfo(GVs);  // 现在我们传入了参数

        // 现在，我们遍历返回的所有调试信息条目
        for (auto *dbgInfo: GVs) {
            if (dbgInfo) {
                auto *var = dbgInfo->getVariable();

                // 如果是全局变量并且有初始值
                if (var && global.hasInitializer()) {
                    std::string name = var->getName().str();
                    std::string file = var->getFilename().str();

                    std::cout << name << ":" << file << std::endl;
                }
            }
        }
    }


    return 0;
}
