#include <iostream>
#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <llvm/IR/Function.h>
#include <llvm/IR/Instruction.h>
#include <llvm/IR/Instructions.h>
#include <llvm/Bitcode/BitcodeReader.h>
#include <llvm/Support/SourceMgr.h>
#include <llvm/Support/MemoryBuffer.h>
#include <llvm/IR/DebugInfo.h>
#include <llvm/IRReader/IRReader.h>
#include <llvm/IR/GlobalVariable.h>
#include <set>
#include <llvm/IR/Type.h>
#include <llvm/IR/DerivedTypes.h>
#include <string>
#include <llvm/Support/ErrorOr.h>


void extractFuncSym(const std::string& IRFile, const std::string& funcName){

    llvm::LLVMContext context;
    llvm::SMDiagnostic error;
    std::unique_ptr<llvm::Module> module = llvm::parseIRFile(IRFile, error, context);

    if (!module) {
        llvm::errs() <<"Not Found IR File\n";
        return;
    }
    // 获取特定函数
    llvm::Function *targetFunc = module->getFunction(funcName);
    if (!targetFunc) {
        llvm::errs() << "Function not found!\n";
        return;
    }
    // Traverse all functions in the module
    auto & func = *targetFunc;
        if (!func.isDeclaration()) {
            // Traverse all instructions in the function
            for (auto &bb : func) {
                for (auto &inst : bb) {
                    if (auto callInst = llvm::dyn_cast<llvm::CallInst>(&inst)) {
                        llvm::Function *calledFunc = callInst->getCalledFunction();
                        if (calledFunc && !calledFunc->isIntrinsic()) {
                            std::cout <<calledFunc->getName().str()<<":";

                            if (auto *subProgram = calledFunc->getSubprogram()) {
                                std::cout << subProgram->getFile()->getFilename().str();
                            }
                            std::cout << std::endl;
                        }
                    }
                }
            }
        }


}


int main(int argc, char **argv) {
     if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " <IR file> <Func name> \n";
        return 1;
    }
    extractFuncSym(std::string(argv[1]),std::string(argv[2]));

    return 0;
}
