#include "llvm/IR/Module.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/AbstractCallSite.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/IntrinsicInst.h"
#include "llvm/IR/Value.h"
#include "llvm/IR/GlobalAlias.h"
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

    std::set<Value *> uninitializedGlobalVariableFunction;

    std::map<Value *, std::set<Function *>> pts_func;

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

        CallGraphNode *CGN = CG[Caller];
        bool edgeExists = false;

        for (auto &Pair: *CGN) {
            // Pair 是一个 std::pair<WeakVH, CallGraphNode*> 对象
            CallGraphNode *calleeNode = Pair.second;

            if (calleeNode->getFunction() == Callee) {
                edgeExists = true; // 边存在！
                break;
            }
        }
        if (!edgeExists)
            // add new edge
            CallerNode->addCalledFunction(nullptr, CalleeNode);
    }

    void DealingInstructionForGV(Module &M, CallGraph &CG, Function &F, Instruction &I) {
        // 递归函数，处理可能嵌套的全局变量引用
        std::function<void(Value *)> processOperand = [&](Value *V) {
            if (!V) return;

            // 处理全局变量
            if (auto *GV = dyn_cast<GlobalVariable>(V)) {
                addNodeAndEdge(CG, &F, getFunction(M, GV->getName().str()));
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

        // 遍历指令的所有操作数
        for (Use &U: I.operands()) {
            processOperand(U.get());
        }
    }

    void DealingInstructionForFunctionDirectly(Module &M, CallGraph &CG, Function &F, Instruction &I) {
        // 递归函数，处理可能嵌套的函数直接引用
        std::function<void(Value *)> processOperand = [&](Value *V) {
            if (!V) return;
            // 处理函数
            if (auto *GV =  dyn_cast<GlobalVariable>(V) ){
                return;
            }else if (auto *referencedFunc = dyn_cast<Function>(V)) {
                pts_func[V].insert(referencedFunc);
                pts_func[&I].insert(referencedFunc);
                addNodeAndEdge(CG, &F, referencedFunc);
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
            else if (auto *C = dyn_cast<Constant>(V)) {
                for (auto &Op: C->operands()) {
                    processOperand(Op);

                }
            }
        };

        // 遍历指令的所有操作数
        for (Use &U: I.operands()) {
            processOperand(U.get());
        }
    }

    void DealingInitializers(Module &M, CallGraph &CG) {
        // deal with initializers
        for (GlobalVariable &GV: M.globals()) {
            if (GV.hasInitializer()) {
                Constant *Init = GV.getInitializer();
                std::string globalVarName = GV.getName().str();
//                outs()<< globalVarName << "\n";
                initializedGlobalVariableFunction.insert(getFunction(M, globalVarName));
                auto* F = getFunction(M, globalVarName);
                if (Init) {
//                    outs()<< *Init << "\n";
                    if (auto *func = dyn_cast<Function>(Init)) {
                        addNodeAndEdge(CG, getFunction(M, globalVarName), func);
                    } else {
                        std::function<void(Value *)> processOperand = [&](Value *V) {
                            if (!V) return;
                            // 处理函数
                            if (auto *GV =  dyn_cast<GlobalVariable>(V) ){
                                addNodeAndEdge(CG, F, getFunction(M, GV->getName().str()));
                                return;
                            }else if (auto *referencedFunc = dyn_cast<Function>(V)) {
                                addNodeAndEdge(CG, F, referencedFunc);
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
                            else if (auto *C = dyn_cast<Constant>(V)) {
                                for (auto &Op: C->operands()) {
                                    processOperand(Op);
                                }
                            }
                        };
                        processOperand(Init);
                    }
                }
            }else {
                std::string globalVarName = GV.getName().str();
                uninitializedGlobalVariableFunction.insert(getFunction(M, globalVarName));
            }
        }

        // 遍历函数中的所有指令，查找这些全局变量的使用
        for (Function &F: M) {
            std::string functionName = F.getName().str();
            for (BasicBlock &BB: F) {
                for (Instruction &I: BB) {
                    DealingInstructionForGV(M, CG, F, I);
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

            for (BasicBlock &BB: *F) {
                for (Instruction &I: BB) {
                    switch (I.getOpcode()) {
                        case Instruction::Store: {
                            // 处理Store指令
                            auto *Store = dyn_cast<StoreInst>(&I);
                            Value *valueOperand = Store->getValueOperand();
                            Value *pointerOperand = Store->getPointerOperand();
                            if (auto *involvedFunc = dyn_cast<Function>(valueOperand)) {
                                pts_func[valueOperand].insert(involvedFunc);  //although some involveFuncs are not inst
                                pts_func[pointerOperand].insert(involvedFunc);
                                // 只要取得地址，就加入依赖列表
                                addNodeAndEdge(CG, F, involvedFunc);
                            } else {
                                // 不确定valueOperand
                                if (!pts_func[valueOperand].empty()) {
                                    for (auto *i: pts_func[valueOperand]) {
                                        pts_func[pointerOperand].insert(i);
                                    }
                                }
                            }
                            // 检查是否修改了全局变量，或者全局变量的别名
                            if (isa<GlobalVariable>(pointerOperand)) {
                                if (auto *globalVar = dyn_cast<GlobalVariable>(pointerOperand)) {
                                    std::string name = globalVar->getName().str();
                                    if (!pts_func[valueOperand].empty()) {
                                        for (auto &i: pts_func[valueOperand]) {
                                            addNodeAndEdge(CG, getFunction(M, name), i);
                                        }
                                    }
                                }
                            } else if (auto *GA = dyn_cast<GlobalAlias>(pointerOperand)) {
                                if (auto *Aliasee = GA->getAliasee()) {
                                    // The aliasee can be another global variable
                                    if (auto *globalVar = dyn_cast<GlobalVariable>(Aliasee)) {
                                        std::string name = globalVar->getName().str();
                                        if (!pts_func[valueOperand].empty()) {
                                            for (auto &i: pts_func[valueOperand]) {
                                                addNodeAndEdge(CG, getFunction(M, name), i);
                                            }
                                        }
                                    }
                                }
                            }
                            break;
                        }

                        case Instruction::Call: // 适用于普通调用
                        case Instruction::Invoke: { // 适用于可能引起异常的调用
                            // 处理Call指令
                            auto *call = dyn_cast<CallBase>(&I);
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
                                // 判断arg的类型是不是GV，保守分析GV可能指向的函数,
                                // 但是结果不影响init_reach函数的识别，也不影响最后模块建议
                                /*
                                for (User::op_iterator arg = call->arg_begin(); arg != call->arg_end(); ++arg) {
                                    Value *argValue = *arg;
                                    if (auto *GV = dyn_cast<GlobalVariable>(arg)) {
                                        for (auto *F: pts_func[&I]) {
                                            addNodeAndEdge(CG, getFunction(M, GV->getName().str()), F);
                                        }
                                    }
                                }
                                 */

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

                            break;
                        }
                            // 根据需要添加更多的case语句来处理不同的指令类型

                        default: {
                            DealingInstructionForFunctionDirectly(M, CG, *F, I);
                            break;
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
                F->setName(NewName);}
            else if (F && uninitializedGlobalVariableFunction.count(F)){
                std::string NewName = F->getName().str();
                NewName += "@isDeclaration";
                F->setName(NewName);
            } else if (F && F->isDeclaration()) {
                std::string NewName = F->getName().str();
                NewName += "@isDeclaration";
                F->setName(NewName);
            } else if (F && !F->isDeclaration() ) {
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
static RegisterPass<Steensggard> X("Steensggard", "Steensggard pointer analysis Pass", true, true);

// /home/plot/Linux-modular/TypeDive/mlta/llvm-project/prefix/bin/opt -load ./Steensggard.so -Steensggard    -enable-new-pm=0   example/example.bc
///home/plot/Linux-modular/TypeDive/mlta/llvm-project/prefix/bin/opt -load ./Steensggard.so -Steensggard    -enable-new-pm=0    /home/plot/hn_working_dir/Linux-modular/Frontend/Kernel_src/arch/x86/pci/fixup.bc

