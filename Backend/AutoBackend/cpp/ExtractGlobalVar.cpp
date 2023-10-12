//
// Created by david on 9/25/23.
//
#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <iostream>
#include <llvm/IRReader/IRReader.h>
#include <llvm/Support/SourceMgr.h>
#include <llvm/IR/Instructions.h>
#include <llvm/IR/GlobalVariable.h>
#include <llvm/IR/DebugInfoMetadata.h>
#include <llvm/IR/DebugInfo.h>

#include <set>

#include <llvm/IR/Type.h>
#include <llvm/IR/DerivedTypes.h>
#include <string>

//std::string llvmTypeToCpp(llvm::Type* type) {
//    if (!type) return "void";
//
//    switch (type->getTypeID()) {
//        case llvm::Type::VoidTyID:
//            return "void";
//        case llvm::Type::IntegerTyID: {
//            llvm::IntegerType* intType = llvm::cast<llvm::IntegerType>(type);
//            switch (intType->getBitWidth()) {
//                case 1:
//                    return "bool";
//                case 8:
//                    return "char";
//                case 16:
//                    return "short";
//                case 32:
//                    return "int";
//                case 64:
//                    return "long long";
//                default:
//                    return "unknown_int_type";
//            }
//        }
//        case llvm::Type::FloatTyID:
//            return "float";
//        case llvm::Type::DoubleTyID:
//            return "double";
//        case llvm::Type::PointerTyID:
//            return llvmTypeToCpp(type->getPointerElementType()) + "*";
//        case llvm::Type::ArrayTyID: {
//            llvm::ArrayType* arrType = llvm::cast<llvm::ArrayType>(type);
//            return llvmTypeToCpp(arrType->getElementType()) + "*";
//        }
//        case llvm::Type::StructTyID: {
//            llvm::StructType* structType = llvm::dyn_cast<llvm::StructType>(type);
//            if (structType && structType->hasName()) {
//                return structType->getName().str();
//            } else {
//                return "anonymous_struct";
//            }
//        }
//        default:
//            return "unknown_type";
//    }
//}
void extractGlobalVarLoc(llvm::GlobalVariable *globalVar) {
    // 打印全局符号的名称和类型
    llvm::errs() << globalVar->getName();
    // 打印全局变量定义的文件位置
    if (globalVar->hasMetadata()) {
        llvm::MDNode *node = globalVar->getMetadata("dbg");
        if (llvm::DIGlobalVariable * DGV = llvm::dyn_cast<llvm::DIGlobalVariable>(node)) {
            llvm::DIFile *file = DGV->getFile();
            if (file) {
                llvm::errs() << ":" << file->getFilename().str();
            }else {
                llvm::errs() << "no filename";
            }
        }
        if (llvm::DILocation * loc = llvm::dyn_cast<llvm::DILocation>(node)) {
            llvm::errs() << ":" << loc->getFilename();
        }
    } else {
        llvm::errs() << "no metadata";
    }
    llvm::errs() << "\n";
}


void extractGlobalVar(const std::string &IRFile, const std::string &funcName) {
    llvm::LLVMContext context;
    llvm::SMDiagnostic error;
    std::unique_ptr <llvm::Module> module = llvm::parseIRFile(IRFile, error, context);


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

    for (auto &block: *targetFunc) {
        for (auto &instr: block) {
            // 对于每个指令，检查其使用的值
            for (unsigned i = 0, e = instr.getNumOperands(); i != e; ++i) {
                llvm::Value *op = instr.getOperand(i);
                if (llvm::GlobalVariable * globalVar = llvm::dyn_cast<llvm::GlobalVariable>(op)) {
                    extractGlobalVarLoc(globalVar);
                } else if (auto *constant = llvm::dyn_cast<llvm::Constant>(op)) {
                    for (auto &i: constant->operands()) {
                        if (auto *globalVar = llvm::dyn_cast<llvm::GlobalVariable>(i)) {
                            extractGlobalVarLoc(globalVar);
                        }
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

    extractGlobalVar(std::string(argv[1]), std::string(argv[2]));

    return 0;
}
