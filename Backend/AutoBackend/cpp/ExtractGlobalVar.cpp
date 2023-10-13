//
// Created by david on 9/25/23.
//
#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <iostream>
#include "llvm/BinaryFormat/Dwarf.h"

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


bool hasOtherChars(const std::string &str) {
    for (char c: str) {
        if (c != '*' && c != ' ') {
            return true;
        }
    }
    return false;
}


std::string replaceStructDotWithSpace(const std::string &input) {
    std::string result = input;
    std::string target = "struct.";
    std::string replacement = "struct ";

    size_t pos = 0;
    while ((pos = result.find(target, pos)) != std::string::npos) {
        result.replace(pos, target.length(), replacement);
        pos += replacement.length();
    }

    return result;
}


std::string llvmTypeToCpp(llvm::Type *type) {
    if (!type) return "";

    switch (type->getTypeID()) {
        case llvm::Type::VoidTyID:
            return "void";
        case llvm::Type::IntegerTyID: {
            auto *intType = llvm::cast<llvm::IntegerType>(type);
            switch (intType->getBitWidth()) {
                case 1:
                    return "bool";
                case 8:
                    return "char";
                case 16:
                    return "short";
                case 32:
                    return "int";
                case 64:
                    return "long long";
                default:
                    return "";
            }
        }
        case llvm::Type::FloatTyID:
            return "float";
        case llvm::Type::DoubleTyID:
            return "double";
        case llvm::Type::PointerTyID:
            return llvmTypeToCpp(type->getPointerElementType()) + " *";
        case llvm::Type::ArrayTyID: {
            auto *arrType = llvm::cast<llvm::ArrayType>(type);
            return llvmTypeToCpp(arrType->getElementType()) + " *";
        }
        case llvm::Type::StructTyID: {
            auto *structType = llvm::dyn_cast<llvm::StructType>(type);
            if (structType && structType->hasName()) {
                return replaceStructDotWithSpace(structType->getName().str());
            } else {
                return "";
            }
        }
        default:
            return "";
    }
}

void dealDIType(llvm::DIType *type) {
    if (!type->getName().empty()) {
        llvm::outs() << type->getName().str();
    } else {
//        switch (type->getTag()) {
//            case llvm::dwarf::DW_TAG_base_type:
//                llvm::errs() << type->getName().str() << "\n";
//                break;
//
//            case llvm::dwarf::DW_TAG_class_type:
//                llvm::errs() << type->getName().str() << "\n";
//                break;
//
//            case llvm::dwarf::DW_TAG_structure_type:
//                llvm::errs() << type->getName().str() << "\n";
//                break;
//
//            case llvm::dwarf::DW_TAG_union_type:
//                llvm::errs() << type->getName().str() << "\n";
//                break;
//
//            case llvm::dwarf::DW_TAG_enumeration_type:
//                llvm::errs() << type->getName().str() << "\n";
//                break;
//
//            case llvm::dwarf::DW_TAG_typedef:
//                llvm::errs() << type->getName().str() << "\n";
//                break;
//
//            case llvm::dwarf::DW_TAG_pointer_type:
//            case llvm::dwarf::DW_TAG_array_type:
//            case llvm::dwarf::DW_TAG_subroutine_type:
//            default:
//                llvm::errs() << "\n";
//        }
    }
}

void extractGlobalVar(llvm::GlobalVariable *globalVar) {
    // 打印全局符号的名称
    llvm::outs() << globalVar->getName() << ":";
    std::string typeStr;
    //GlobalVariable的类型总是一个指针类型。这是因为全局变量代表的是一个内存地址，所以它们的类型是指向其数据的指针

    typeStr.append(llvmTypeToCpp(globalVar->getValueType()));
    if (!typeStr.empty() && hasOtherChars(typeStr)) {
        if (globalVar->isConstant()) {
            llvm::outs() << "const ";
        }
        llvm::outs() << typeStr << "\n";
    } else if (globalVar->hasMetadata()) {
        llvm::MDNode *node = globalVar->getMetadata("dbg");
        if (auto *GVExpr = llvm::dyn_cast<llvm::DIGlobalVariableExpression>(node)) {
            llvm::DIGlobalVariable *DGV = GVExpr->getVariable();
            llvm::DIType *type = DGV->getType();
            // 打印全局变量的源代码类型
            dealDIType(type);
        } else if (auto *DGV = llvm::dyn_cast<llvm::DIGlobalVariable>(node)) {
            llvm::DIType *type = DGV->getType();
            // 打印全局变量的源代码类型
            dealDIType(type);

        } else {
            llvm::outs() << "";
        }
        llvm::outs() << "\n";
    }
}


void dealFunc(const llvm::Module &module, const llvm::Function &function) {
    for (auto &block: function) {
        for (auto &instr: block) {
            // 对于每个指令，检查其使用的值
            for (unsigned i = 0, e = instr.getNumOperands(); i != e; ++i) {
                llvm::Value *op = instr.getOperand(i);
                if (auto *GV = llvm::dyn_cast<llvm::GlobalVariable>(op)) {
                    extractGlobalVar(GV);
                } else if (auto *constant = llvm::dyn_cast<llvm::Constant>(op)) {
                    for (auto &U: constant->operands()) {
                        if (auto *globalVar = llvm::dyn_cast<llvm::GlobalVariable>(U)) {
                            extractGlobalVar(globalVar);
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

    llvm::LLVMContext context;
    llvm::SMDiagnostic error;
    std::unique_ptr<llvm::Module> module = llvm::parseIRFile(std::string(argv[1]), error, context);

    if (!module) {
        llvm::errs() << "Not Found IR File\n";
        return -1;
    }
    std::string funcName = std::string(argv[2]);
//    for (llvm::Function &F: *module) {
//        llvm::errs() << "\n";
//        llvm::errs() <<"Function " <<F.getName() << ":\n";
    auto *F = module->getFunction(std::string(argv[2]));
    dealFunc(*module, *F);
//    }
    return 0;
}