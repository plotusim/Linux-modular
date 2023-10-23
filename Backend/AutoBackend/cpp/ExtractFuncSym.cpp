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

using namespace llvm;

void extractFuncSym(const std::string &IRFile, const std::string &funcName) {

    llvm::LLVMContext context;
    llvm::SMDiagnostic error;
    std::unique_ptr<llvm::Module> module = llvm::parseIRFile(IRFile, error, context);

    if (!module) {
        llvm::errs() << "Not Found IR File\n";
        return;
    }
    // 获取特定函数
    llvm::Function *targetFunc = module->getFunction(funcName);
    if (!targetFunc) {
        llvm::errs() << "Function not found!\n";
        return;
    }
    // Traverse all functions in the module
    auto &func = *targetFunc;
    if (!func.isDeclaration()) {
        // Traverse all instructions in the function
        for (auto &bb: func) {
            for (auto &instr: bb) {
                for (unsigned i = 0, e = instr.getNumOperands(); i != e; ++i) {
                    llvm::Value *op = instr.getOperand(i);
                    std::function<void(Value *)> processOperand = [&](Value *V) {
                        if (!V) return;

                        if (auto *calledFunc = dyn_cast<Function>(V)) {
                            if (calledFunc && !calledFunc->isIntrinsic()) {
                                std::cout << calledFunc->getName().str() << ":";

                                if (auto *subProgram = calledFunc->getSubprogram()) {
                                    std::cout << subProgram->getFile()->getFilename().str();
                                }
                                std::cout << std::endl;
                            }
                        }
                        // 处理全局变量
                        if (auto *GV = dyn_cast<GlobalVariable>(V)) {

                        }
                            // 处理别名
                        else if (auto *GA = dyn_cast<GlobalAlias>(V)) {
                            if (auto *Aliasee = GA->getAliasee()) {
                                // The aliasee can be another global variable, function, or another alias
                                // Here we recursively process the aliasee, since it's also a 'Value*'
                                processOperand(Aliasee);
                            }
                        }
                            // 处理常量表达式，这可能涉及位转换或其他间接引用
                        else if (auto *CE = dyn_cast<ConstantExpr>(V)) {
                            // 递归处理操作数
                            for (unsigned i = 0, e = CE->getNumOperands(); i != e; ++i) {
                                processOperand(CE->getOperand(i));
                            }
                        }
                            // 如果是其他类型的常量，检查其是否包含全局变量
                        else if (auto *C = dyn_cast<Constant>(V)) {
                            for (auto &Op: C->operands()) {
                                processOperand(Op);
                            }
                        }
                    };
                    processOperand(op);
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
    extractFuncSym(std::string(argv[1]), std::string(argv[2]));

    return 0;
}
