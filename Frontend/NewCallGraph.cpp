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
#include <vector>


using namespace llvm;

class NewCallGraph : public ModulePass {
public:
    static char ID;
    NewCallGraph() : ModulePass(ID) {}

    void addNodeAndEdge(CallGraph &CG, Function *Caller, Function *Callee) {
        // add new node
        CallGraphNode *CallerNode = CG.getOrInsertFunction(Caller);
        CallGraphNode *CalleeNode = CG.getOrInsertFunction(Callee);

        // add new edge
        CallerNode->addCalledFunction(nullptr, CalleeNode);
    }

    std::vector<llvm::Function*> initFunctions;
    llvm::Function* initVirtualFunc;

    void AddInternal(const Constant *CV) {
        // ConstantArray
        if (const ConstantArray *CA = dyn_cast<ConstantArray>(CV)) {
            // output first element
            AddFunctionInternal(CA->getOperand(0));
            for (unsigned i = 1, e = CA->getNumOperands(); i != e; ++i) {
                AddFunctionInternal(CA->getOperand(i));
            }
            return;
        }

        // ConstantStruct
        if (const ConstantStruct *CS = dyn_cast<ConstantStruct>(CV)) {
            unsigned N = CS->getNumOperands();
            if (N) {
                // first element
                AddFunctionInternal(CS->getOperand(0));
                for (unsigned i = 1; i < N; i++) {
                    AddFunctionInternal(CS->getOperand(i));
                }
            }
            return;
        }
    }

    void AddFunctionInternal(const Value *V) {
        if (V->hasName()) {
            if(const Function *func = dyn_cast<Function>(V)) {
                initFunctions.push_back(const_cast<llvm::Function *>(dyn_cast<Function>(V)));
                return;
            }
            return;
        }

        // if is constant
        const Constant *CV = dyn_cast<Constant>(V);
        if(CV){
            AddInternal(CV);
        }
    }

    void DealingInitializers(Module &M, CallGraph &CG) {
        // deal with initializers
        for(GlobalVariable &GV : M.globals()) {
            Constant *Init = GV.getInitializer();
            if (Init) {
                if(Function *func = dyn_cast<Function>(Init)) {
                    initFunctions.push_back(const_cast<llvm::Function *>(dyn_cast<Function>(Init)));
                }
                else {
                    // deal with struct or array
                    AddInternal(Init);
                }
            }
        }

        // if no initialize funcs then skip initializeing initFunction
        // if there are/is init funcs, we initialize a initVirtualFunc as virtual node to connect them
        if(initFunctions.size() != 0 ) {
            std::string moduleName = M.getName().str();
            // use modulename + __virtual_init as virtual node's name
            std::string initVirtualFunctionName = moduleName + "__virtual_init";
            // create a function, set it accessable for other modules
            initVirtualFunc = Function::Create(FunctionType::get(Type::getVoidTy(M.getContext()), false), 
                                                GlobalValue::LinkageTypes::ExternalLinkage, 
                                                initVirtualFunctionName, 
                                                &M);
            // make edge and node with funcs in initFunctions with initVirtualFunc
            for(Function* F : initFunctions) {
                addNodeAndEdge(CG, initVirtualFunc, F);
            }
        }
    }

    void AnalysisLoadInst(Module &M, CallGraph &CG) {
        // make a copy
        std::vector<CallGraphNode *> CallGraphNodes;
        for(auto iter = CG.begin(); iter != CG.end(); ++iter) {
            std::unique_ptr<CallGraphNode> &NodePtr = iter->second;
            CallGraphNode *Node = NodePtr.get();
            CallGraphNodes.push_back(Node);
        }

        // analysis all nodes
        for(CallGraphNode *Node : CallGraphNodes) {
            Function *F = Node->getFunction();
            if(F == nullptr) continue;
            
            // analysis load instructions
            for(BasicBlock &BB : *F) {
                for(Instruction &I : BB) {
                    // identify load inst
                    if(LoadInst *Load = dyn_cast<LoadInst>(&I)){
                        Value *PointerOperand = Load->getPointerOperand();
                        if(CallInst *Call = dyn_cast<CallInst>(PointerOperand)) {
                            Function * CalledFunction = Call->getCalledFunction();
                            if(CalledFunction) {
                                Node->addCalledFunction(nullptr, CG.getOrInsertFunction(CalledFunction));
                            }
                        }
                    }
                }
            }
        }
    }
    
    // print
    void outputCallGraph(CallGraph &CG) {
        for (auto &Node : CG) {
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

        AnalysisLoadInst(M, CG);

        outputCallGraph(CG);

        return false;
    }

    void getAnalysisUsage(AnalysisUsage &AU) const override {
        AU.setPreservesAll();
        AU.addRequiredTransitive<CallGraphWrapperPass>();
    }
};

char NewCallGraph::ID = 0;

// true: module pass   ;   true: won't change module
static RegisterPass<NewCallGraph> X("NewCallGraph", "New Call Graph Pass", true, true);
