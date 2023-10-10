
#include "llvm/IR/Module.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/AbstractCallSite.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/IntrinsicInst.h"
#include "llvm/IR/Value.h"
#include "llvm/IR/PassManager.h"
#include "llvm/Pass.h"
#include "llvm/Analysis/CallGraph.h"
#include "llvm/Analysis/CallGraphSCCPass.h"
#include "llvm/ADT/SCCIterator.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/Config/llvm-config.h"
#include "llvm/InitializePasses.h"
#include "llvm/Support/Compiler.h"
#include "llvm/Support/Debug.h"
#include "llvm/Support/raw_ostream.h"
#include <set>
#include <algorithm>
#include <iostream>

using namespace llvm;

class Steensggard : public ModulePass {
public:
    static char ID;

    Steensggard() : ModulePass(ID) {}

    std::map<std::string, Function *> string2Function;

    std::set<Value *> initializedGlobalVariableFunction;

    Function *getFunction(Module &M, const std::string &globalVarName) {
        if (string2Function.find(globalVarName) == string2Function.end()) {
            return string2Function[globalVarName] = Function::Create(
                    FunctionType::get(Type::getVoidTy(M.getContext()), false),
                    GlobalValue::LinkageTypes::ExternalLinkage,
                    "__virtual__global__" + globalVarName,
                    &M);
        } else {
            return string2Function[globalVarName];
        }
    }


    void addNodeAndEdge(CallGraph &CG, const Function *Caller, const Function *Callee) {
        // add new node
        CallGraphNode *CallerNode = CG.getOrInsertFunction(Caller);
        CallGraphNode *CalleeNode = CG.getOrInsertFunction(Callee);

        // add new edge
        CallerNode->addCalledFunction(nullptr, CalleeNode);
    }

//    std::set<llvm::Function *> initFunctions;
//    llvm::Function *initVirtualFunc;

    std::map<Value *, std::set<Function *>> pts_func;

    void AddInternal(CallGraph &CG, Module &M, const Constant *CV, const std::string &globalVarName) {
        // ConstantArray
        if (const ConstantArray *CA = dyn_cast<ConstantArray>(CV)) {
            // output first element
            AddFunctionInternal(CG, M, CA->getOperand(0), globalVarName);
            for (unsigned i = 1, e = CA->getNumOperands(); i != e; ++i) {
                AddFunctionInternal(CG, M, CA->getOperand(i), globalVarName);
            }
            return;
        }

        // ConstantStruct
        if (const ConstantStruct *CS = dyn_cast<ConstantStruct>(CV)) {
            unsigned N = CS->getNumOperands();
            if (N) {
                // first element
                AddFunctionInternal(CG, M, CS->getOperand(0), globalVarName);
                for (unsigned i = 1; i < N; i++) {
                    AddFunctionInternal(CG, M, CS->getOperand(i), globalVarName);
                }
            }
            return;
        }
    }

    void AddFunctionInternal(CallGraph &CG, Module &M, const Value *V, const std::string &globalVarName) {
        if (V->hasName()) {
            if (const Function *func = dyn_cast<Function>(V)) {
                addNodeAndEdge(CG, getFunction(M, globalVarName), func);
                return;
            }
            if (auto *GV = dyn_cast<GlobalVariable>(V)) {
                addNodeAndEdge(CG, getFunction(M, globalVarName), getFunction(M, GV->getName().str()));
                return;
            }
        }

        // if is constant
        const Constant *CV = dyn_cast<Constant>(V);
        if (CV) {
            AddInternal(CG, M, CV, globalVarName);
        }
    }

    void DealingInstruction(Module &M, CallGraph &CG, Function &F, Instruction &I) {
//        outs() << F.getName() << ":\t ";
//        I.print(outs());
//        outs() << "\n";
        for (Use &U: I.operands()) {
            Value *OperandValue = U.get();
            if (OperandValue) {
                if (auto *UsedGV = dyn_cast<GlobalVariable>(OperandValue)) {
                    std::string globalVarName = UsedGV->getName().str();
                    addNodeAndEdge(CG, &F, getFunction(M, globalVarName));
                } else if (isa<PointerType>(OperandValue->getType())) {
                    if (auto *constant = dyn_cast<Constant>(OperandValue)) {
                        for (auto &i: constant->operands()) {
                            if (auto *GV = dyn_cast<GlobalVariable>(i)) {
                                addNodeAndEdge(CG, &F, getFunction(M, GV->getName().str()));
                            }
                        }
                    }
                }
            }
        }
    }

    void DealingInitializers(Module &M, CallGraph &CG) {
        // deal with initializers
        for (GlobalVariable &GV: M.globals()) {
            if (GV.hasInitializer()) {
                Constant *Init = GV.getInitializer();
                std::string globalVarName = GV.getName().str();
                initializedGlobalVariableFunction.insert(getFunction(M, globalVarName));
                if (Init) {
                    if (Function *func = dyn_cast<Function>(Init)) {
                        addNodeAndEdge(CG, getFunction(M, globalVarName), func);
                    } else {
                        // deal with struct or array
                        AddInternal(CG, M, Init, globalVarName);
                    }
                }
            }
        }


        // 遍历函数中的所有指令，查找这些全局变量的使用
        for (Function &F: M) {
            std::string functionName = F.getName().str();
            for (BasicBlock &BB: F) {
                for (Instruction &I: BB) {
                    DealingInstruction(M, CG, F, I);
                }
            }
        }

    }

    void AnalysisFunctionCall(Module &M, CallGraph &CG) {
        // make a copy
        std::vector<CallGraphNode *> CallGraphNodes;
        for (auto iter = CG.begin(); iter != CG.end(); ++iter) {
            std::unique_ptr<CallGraphNode> &NodePtr = iter->second;
            CallGraphNode *Node = NodePtr.get();
            CallGraphNodes.push_back(Node);
        }

        // analysis all nodes
        for (CallGraphNode *Node: CallGraphNodes) {
            Function *F = Node->getFunction();
            if (F == nullptr) continue;


            //从这里，重写steensggard算法
            for (BasicBlock &BB: *F) {
                for (Instruction &I: BB) {
                    if (StoreInst *Store = dyn_cast<StoreInst>(&I)) {
                        Value *valueOperand = Store->getValueOperand();
                        Value *pointerOperand = Store->getPointerOperand();
                        if (auto *involvedFunc = dyn_cast<Function>(valueOperand)) {
                            pts_func[valueOperand].insert(involvedFunc);  //although some involveFuncs are not inst
                            pts_func[pointerOperand].insert(involvedFunc);

                            // 只要取得地址，就加入依赖列表
                            addNodeAndEdge(CG, F, involvedFunc);
                        } else {
                            if (!pts_func[valueOperand].empty()) {
                                for (auto *i: pts_func[valueOperand]) {
                                    pts_func[pointerOperand].insert(i);
                                }
                            }
                        }
                        // 检查是否修改了全局变量
                        if (isa<GlobalVariable>(pointerOperand)) {
                            if (auto *globalVar = dyn_cast<GlobalVariable>(pointerOperand)) {
                                std::string name = globalVar->getName().str();
                                if (!pts_func[valueOperand].empty()) {
                                    for (auto &i: pts_func[valueOperand]) {
                                        addNodeAndEdge(CG, getFunction(M, name), i);
                                    }
                                }
                            }
                        }
                    } else if (auto *call = dyn_cast<CallBase>(&I)) {
                        Value *calledValue = call->getCalledOperand();
                        // call 我们分2种情况，直接知道被call的函数与不知道callee函数
                        if (const Function *Callee = dyn_cast<Function>(calledValue)) {
                            addNodeAndEdge(CG, F, Callee);
                            for (User::op_iterator arg = call->arg_begin(); arg != call->arg_end(); ++arg) {
                                Value *argValue = *arg;
                                if (!pts_func[argValue].empty()) {
                                    for (auto iter = pts_func[argValue].begin();
                                         iter != pts_func[argValue].end(); iter++) {
                                        if (Callee != *iter) {
                                            addNodeAndEdge(CG, Callee, *iter);
                                        }

                                    }
                                }
                            }

                        } else if (!pts_func[calledValue].empty()) {
                            for (auto PossibleCallee: pts_func[calledValue]) {
                                addNodeAndEdge(CG, F, PossibleCallee);
                                for (User::op_iterator arg = call->arg_begin(); arg != call->arg_end(); ++arg) {
                                    Value *argValue = *arg;
                                    if (!pts_func[argValue].empty()) {
                                        for (auto iter = pts_func[argValue].begin();
                                             iter != pts_func[argValue].end(); iter++) {
                                            if (Callee != *iter) {
                                                addNodeAndEdge(CG, PossibleCallee, *iter);
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        //如果参数里面有指针类型，则我们认为这个指针类型依赖于其他参数
                        for (User::op_iterator arg = call->arg_begin(); arg != call->arg_end(); ++arg) {
                            Value *argValue = arg->get();
                            if (argValue->getType()->isPointerTy()) {
                                if (!pts_func[&I].empty()) {
                                    for (auto iter = pts_func[&I].begin();
                                         iter != pts_func[&I].end(); iter++) {
                                        pts_func[argValue].insert(*iter);
                                    }
                                }
                            }
                        }

                    } else {
                        for (auto operand = I.operands().begin();
                             operand != I.operands().end(); ++operand) {    //especially phi
                            if (Function *involvedFunc = dyn_cast<Function>(*operand)) {
                                pts_func[*operand].insert(involvedFunc);
                                pts_func[&I].insert(involvedFunc);
                            }   //new
                            if (!pts_func[*operand].empty()) {
                                for (auto iter = pts_func[*operand].begin(); iter != pts_func[*operand].end();
                                     iter++) {
                                    pts_func[&I].insert(*iter);
                                }
                            }
                        }
                    }


                }
            }
        }
    }

    void addSuffix(CallGraph &CG) {
        for (auto &Node: CG) {
            Function *F = Node.second->getFunction();
            if (F && F->getName().str().find("llvm.") != std::string::npos)
                continue;
            else if (F && initializedGlobalVariableFunction.count(F)) {
                std::string NewName = F->getName().str();
                NewName += "@isDefinition";
                F->setName(NewName);
            }  else if (F && F->isDeclaration()) {
                std::string NewName = F->getName().str();
                NewName += "@isDeclaration";
                F->setName(NewName);
            } else if (F && !F->isDeclaration()) {
                std::string NewName = F->getName().str();
                NewName += "@isDefinition";
                F->setName(NewName);
            }
        }
    }

// print
    void outputCallGraph(CallGraph &CG) {
        for (auto &Node: CG) {
            Function *F = Node.second->getFunction();
            if (!F)
                continue;

            outs() << F->getName() << " -> ";
            for (auto it = Node.second->begin(); it != Node.second->end(); ++it) {
                Function *Callee = it->second->getFunction();
                if (Callee)
                    outs() << Callee->getName() << " ";
            }
            outs() << "\n";
        }
    }

    bool runOnModule(Module &M) override {
// 获取 CallGraphWrapperPass 对象
        CallGraph &CG = getAnalysis<CallGraphWrapperPass>().getCallGraph();
        DealingInitializers(M, CG);
        AnalysisFunctionCall(M, CG);
        addSuffix(CG);
        outputCallGraph(CG);
        return false;
    }

    void getAnalysisUsage(AnalysisUsage &AU) const override {
        AU.setPreservesAll();
        AU.addRequiredTransitive<CallGraphWrapperPass>();

    }
};

char Steensggard::ID = 0;
// true: module pass   ;   true: won't change module
static RegisterPass<Steensggard> X("Steensggard", "Steensggard pointer analysis Pass", true, true);

// /home/plot/Linux-modular/TypeDive/mlta/llvm-project/prefix/bin/opt -load ./Steensggard.so -Steensggard    -enable-new-pm=0   example/example.bc
