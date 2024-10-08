/*new call graph pass to deal with initializers and pointer call in funcs*/
#include "llvm/IR/Module.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/AbstractCallSite.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/IntrinsicInst.h"
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


using namespace llvm;

class ExtendedFuncGraph : public ModulePass {
public:
    static char ID;

    ExtendedFuncGraph() : ModulePass(ID) {}

    void addNodeAndEdge(CallGraph &CG, Function *Caller, Function *Callee) {
        // add new node
        CallGraphNode *CallerNode = CG.getOrInsertFunction(Caller);
        CallGraphNode *CalleeNode = CG.getOrInsertFunction(Callee);

        // add new edge
        CallerNode->addCalledFunction(nullptr, CalleeNode);
    }

    std::set<llvm::Function *> initFunctions;
    llvm::Function *initVirtualFunc;

    void DealingInitializers(Module &M, CallGraph &CG) {
        // deal with initializers
        for (GlobalVariable &GV: M.globals()) {
            if (GV.hasInitializer()) {
                Constant *Init = GV.getInitializer();
                if (Init) {
                    std::function<void(Value *, std::set<Value *>)> processOperand = [&](Value *V,
                                                                                         std::set<Value *> prevVs) {
                        if (!V) return;

                        if (prevVs.count(V)) {
                            return;
                        }

                        prevVs.insert(V);
                        // 处理函数
                        if (auto *referencedFunc = dyn_cast<Function>(V)) {
                            initFunctions.insert(const_cast<llvm::Function *>(dyn_cast<Function>(referencedFunc)));
                        }
                            // 处理别名
                        else if (auto *GA = dyn_cast<GlobalAlias>(V)) {
                            if (auto *Aliasee = GA->getAliasee()) {
                                // The aliasee can be another global variable, function, or another alias
                                // Here we recursively process the aliasee, since it's also a 'Value*'
                                processOperand(Aliasee, prevVs);
                            }
                        }
                            // 处理常量表达式，这可能涉及位转换或其他间接引用
                        else if (auto *CE = dyn_cast<ConstantExpr>(V)) {
                            // 递归处理操作数
                            for (unsigned i = 0, e = CE->getNumOperands(); i != e; ++i) {
                                processOperand(CE->getOperand(i), prevVs);
                            }
                        } else if (auto *C = dyn_cast<Constant>(V)) {
                            for (auto &Op: C->operands()) {
                                processOperand(Op, prevVs);
                            }
                        }
                    };
                    processOperand(Init, std::set<Value *>());

                }
            }
        }

        for (auto &GlobalAlias: M.aliases()) {
            // 别名本身的名字
            StringRef AliasName = GlobalAlias.getName();

            // 此别名所指向的实际内容
            if (auto *Aliasee = GlobalAlias.getAliasee()) {
                if (auto *F = dyn_cast<Function>(Aliasee))
                    initFunctions.insert(const_cast<llvm::Function *>(dyn_cast<Function>(F)));
            }
        }


//         if no initialize funcs then skip initializeing initFunction
//         if there are/is init funcs, we initialize a initVirtualFunc as virtual node to connect them
        if (initFunctions.size() != 0) {
            std::string str = M.getName().str();
            // use modulename + __virtual_init as virtual node's name
            std::replace(str.begin(), str.end(), '/', '_');
            std::replace(str.begin(), str.end(), '.', '_');
            std::replace(str.begin(), str.end(), '-', '_');
            std::string initVirtualFunctionName = str + "__virtual_init";
            // create a function, set it accessable for other modules
            initVirtualFunc = Function::Create(FunctionType::get(Type::getVoidTy(M.getContext()), false),
                                               GlobalValue::LinkageTypes::ExternalLinkage,
                                               initVirtualFunctionName,
                                               &M);
            CG.getOrInsertFunction(initVirtualFunc);
            // make edge and node with funcs in initFunctions with initVirtualFunc
            for (Function *F: initFunctions) {
                addNodeAndEdge(CG, initVirtualFunc, F);
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
            //if(F->getName() == "__SCT__tp_func_drv_abort_pmsr")
            //outs() << F->getName() << "____\n";

            // analysis load instructions
            for (BasicBlock &BB: *F) {
                for (Instruction &I: BB) {
                    // identify load inst
                    // for situation: a = g();
                    if (LoadInst *Load = dyn_cast<LoadInst>(&I)) {
                        Value *PointerOperand = Load->getPointerOperand();
                        if (CallInst *Call = dyn_cast<CallInst>(PointerOperand)) {
                            Function *CalledFunction = Call->getCalledFunction();
                            if (CalledFunction) {
                                Node->addCalledFunction(nullptr, CG.getOrInsertFunction(CalledFunction));
                            }

                        }
                    }
                        // identify store inst
                        // for situation: func_ptr = &func; / func_ptr = foo_ptr;
                    else if (StoreInst *Store = dyn_cast<StoreInst>(&I)) {
                        Value *valueOperand = Store->getValueOperand();
                        if (Function *involvedFunc = dyn_cast<Function>(valueOperand)) {
                            if (involvedFunc) {
                                Node->addCalledFunction(nullptr, CG.getOrInsertFunction(involvedFunc));
                            }
                        }
                    }
                        // identify phi inst
                    else if (PHINode *phi = dyn_cast<PHINode>(&I)) {
                        for (int i = 0; i < phi->getNumIncomingValues(); ++i) {
                            Value *valueOperand = phi->getIncomingValue(i);
                            if (Function *involvedFunc = dyn_cast<Function>(valueOperand)) {
                                if (involvedFunc) {
                                    Node->addCalledFunction(nullptr, CG.getOrInsertFunction(involvedFunc));
                                    //llvm::outs() << "phi: " << involvedFunc->getName() << "\n";
                                }
                            }
                        }
                    }

                    // add for callInst
                    if (auto *Call = dyn_cast<CallBase>(&I)) {
                        const Function *Callee = Call->getCalledFunction();
                        for (int I = 0, E = Call->arg_size(); I < E; ++I) {
                            Value *valueOperand = Call->getArgOperand(I);
                            //if (!valueOperand->getType()->isPointerTy())
                            //continue;
                            if (Function *involvedFunc = dyn_cast<Function>(valueOperand)) {
                                if (involvedFunc) {
                                    Node->addCalledFunction(nullptr, CG.getOrInsertFunction(involvedFunc));
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
            else if (F && F->isDeclaration()) {
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

char ExtendedFuncGraph::ID = 0;

// true: module pass   ;   true: won't change module
static RegisterPass<ExtendedFuncGraph> X("ExtendedFuncGraph", "Extended Func Graph Pass", true, true);

// /home/plot/Linux-modular/TypeDive/mlta/llvm-project/prefix/bin/opt -load ./ExtendedFuncGraph.so -ExtendedFuncGraph    -enable-new-pm=0   /home/plot/hn_working_dir/Linux-modular/Frontend/Kernel_src/arch/x86/pci/fixup.bc