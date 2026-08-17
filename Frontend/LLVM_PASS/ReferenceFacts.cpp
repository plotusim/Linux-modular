/*
 * Emit linkage-aware reference and pointer constraints as JSON Lines.
 *
 * The pass deliberately does not solve points-to constraints inside one LLVM
 * module.  Per-TU facts are merged and solved to a fixed point by
 * kernel_modularizer.pointer_analysis.
 */
#include "llvm/ADT/SmallPtrSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/Config/llvm-config.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/DataLayout.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/GlobalAlias.h"
#include "llvm/IR/GlobalVariable.h"
#include "llvm/IR/InlineAsm.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/Operator.h"
#include "llvm/Pass.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cctype>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

using namespace llvm;

static cl::opt<std::string> ReferenceFactsSourceRoot(
    "reference-facts-source-root",
    cl::desc("Source root removed from translation-unit and debug paths"),
    cl::init(""));

namespace {

class ReferenceFactsEmitter {
public:
    void run(Module &M) {
        reset();
        Layout = &M.getDataLayout();
        TranslationUnit = normalizePath(
            M.getSourceFileName().empty() ? M.getName() : M.getSourceFileName());
        emitHeader();
        assignStableValueIds(M);
        discoverExportedSymbols(M);
        discoverSyscallEntryFunctions(M);
        emitNodes(M);
        emitGlobalInitializers(M);
        emitFunctionFacts(M);
        outs() << Header << "\n";
        for (const std::string &Record : Records)
            outs() << Record << "\n";
    }

private:
    std::string TranslationUnit;
    std::map<const Value *, std::string> ValueIds;
    std::map<const Function *, std::string> FunctionIds;
    std::map<const GlobalVariable *, std::string> GlobalIds;
    SmallPtrSet<const Function *, 32> ExportedFunctions;
    SmallPtrSet<const GlobalVariable *, 32> ExportedGlobals;
    SmallPtrSet<const Function *, 32> SyscallEntryFunctions;
    std::set<std::string> Records;
    std::string Header;
    unsigned ConstantCounter = 0;
    const DataLayout *Layout = nullptr;

    void reset() {
        TranslationUnit.clear();
        ValueIds.clear();
        FunctionIds.clear();
        GlobalIds.clear();
        ExportedFunctions.clear();
        ExportedGlobals.clear();
        SyscallEntryFunctions.clear();
        Records.clear();
        Header.clear();
        ConstantCounter = 0;
        Layout = nullptr;
    }

    static std::string jsonString(StringRef Input) {
        static const char Hex[] = "0123456789abcdef";
        std::string Result;
        Result.push_back('"');
        for (unsigned char Character : Input.bytes()) {
            switch (Character) {
            case '"':
                Result += "\\\"";
                break;
            case '\\':
                Result += "\\\\";
                break;
            case '\b':
                Result += "\\b";
                break;
            case '\f':
                Result += "\\f";
                break;
            case '\n':
                Result += "\\n";
                break;
            case '\r':
                Result += "\\r";
                break;
            case '\t':
                Result += "\\t";
                break;
            default:
                if (Character < 0x20) {
                    Result += "\\u00";
                    Result.push_back(Hex[(Character >> 4) & 0xf]);
                    Result.push_back(Hex[Character & 0xf]);
                } else {
                    Result.push_back(static_cast<char>(Character));
                }
            }
        }
        Result.push_back('"');
        return Result;
    }

    static std::string stringArray(const std::vector<std::string> &Values) {
        std::string Result = "[";
        for (size_t Index = 0; Index < Values.size(); ++Index) {
            if (Index)
                Result += ",";
            Result += jsonString(Values[Index]);
        }
        Result += "]";
        return Result;
    }

    static std::string optionalString(const std::string &Value) {
        return Value.empty() ? "null" : jsonString(Value);
    }

    std::string normalizePath(StringRef Input) const {
        std::string Path = Input.str();
        std::replace(Path.begin(), Path.end(), '\\', '/');
        std::string Root = ReferenceFactsSourceRoot;
        std::replace(Root.begin(), Root.end(), '\\', '/');
        while (!Root.empty() && Root.back() == '/')
            Root.pop_back();
        if (!Root.empty() && Path.compare(0, Root.size(), Root) == 0) {
            Path.erase(0, Root.size());
            while (!Path.empty() && Path.front() == '/')
                Path.erase(Path.begin());
        } else {
            const std::string Marker = "/Kernel_src/";
            size_t Position = Path.find(Marker);
            if (Position != std::string::npos)
                Path.erase(0, Position + Marker.size());
        }
        while (Path.compare(0, 2, "./") == 0)
            Path.erase(0, 2);
        while (!Path.empty() && Path.front() == '/')
            Path.erase(Path.begin());
        return Path.empty() ? "unknown.bc" : Path;
    }

    static bool isInternal(const GlobalValue &Value) {
        StringRef Name = Value.getName();
#if LLVM_VERSION_MAJOR >= 18
        const bool IsLlvmModuleSymbol = Name.starts_with("llvm.");
#else
        const bool IsLlvmModuleSymbol = Name.startswith("llvm.");
#endif
        // llvm.used, llvm.compiler.used and related appending globals are
        // emitted independently by many translation units.  They are
        // module-local compiler bookkeeping even though their IR linkage is
        // not "internal", so a whole-kernel merge must not conflate them.
        return Value.hasLocalLinkage() || IsLlvmModuleSymbol;
    }

    std::string functionId(const Function &F) {
        auto Existing = FunctionIds.find(&F);
        if (Existing != FunctionIds.end())
            return Existing->second;
        std::string Id = isInternal(F)
                             ? "fn:internal:" + TranslationUnit + ":" +
                                   F.getName().str()
                             : "fn:external:" + F.getName().str();
        FunctionIds[&F] = Id;
        return Id;
    }

    std::string globalId(const GlobalVariable &GV) {
        auto Existing = GlobalIds.find(&GV);
        if (Existing != GlobalIds.end())
            return Existing->second;
        std::string Id = isInternal(GV)
                             ? "global:internal:" + TranslationUnit + ":" +
                                   GV.getName().str()
                             : "global:external:" + GV.getName().str();
        GlobalIds[&GV] = Id;
        return Id;
    }

    static std::string memoryCellObject(const std::string &GlobalId) {
        return "obj:" + GlobalId;
    }

    void emitHeader() {
        Header =
            "{\"metadata\":{\"producer\":\"ReferenceFacts-1\"},"
            "\"record\":\"translation_unit\",\"schema_version\":1,"
            "\"translation_unit\":" +
            jsonString(TranslationUnit) + "}";
    }

    void assignStableValueIds(Module &M) {
        for (Function &F : M) {
            if (F.isIntrinsic())
                continue;
            const std::string FId = functionId(F);
            unsigned ArgumentIndex = 0;
            for (Argument &Argument : F.args()) {
                if (Argument.getType()->isPointerTy())
                    ValueIds[&Argument] =
                        "arg:" + FId + ":" + std::to_string(ArgumentIndex);
                ++ArgumentIndex;
            }
            unsigned InstructionIndex = 0;
            for (BasicBlock &Block : F) {
                for (Instruction &Instruction : Block) {
                    if (Instruction.getType()->isPointerTy())
                        ValueIds[&Instruction] =
                            "val:" + FId + ":tu:" + TranslationUnit + ":" +
                            std::to_string(InstructionIndex);
                    ++InstructionIndex;
                }
            }
        }
    }

    std::string sourcePath(const Function &F) const {
        // A weak/linkonce body may lose to another definition at final link.
        // Do not claim that it is an automatically extractable source owner.
        if (F.isDeclarationForLinker() || F.isWeakForLinker())
            return "";
        const DISubprogram *Subprogram = F.getSubprogram();
        if (!Subprogram)
            return TranslationUnit;
        std::string Path;
        if (!Subprogram->getDirectory().empty())
            Path = (Twine(Subprogram->getDirectory()) + "/" +
                    Subprogram->getFilename()).str();
        else
            Path = Subprogram->getFilename().str();
        return normalizePath(Path);
    }

    void discoverExportedSymbols(Module &M) {
        for (GlobalVariable &GV : M.globals()) {
            if (!GV.hasInitializer())
                continue;
            StringRef Name = GV.getName();
            StringRef Section = GV.getSection();
#if LLVM_VERSION_MAJOR >= 18
            const bool IsKsymtabName = Name.starts_with("__ksymtab_");
#else
            const bool IsKsymtabName = Name.startswith("__ksymtab_");
#endif
            if (!IsKsymtabName &&
                !Section.contains("__ksymtab"))
                continue;
            SmallPtrSet<Function *, 8> Functions;
            SmallPtrSet<Value *, 16> FunctionVisited;
            collectFunctions(GV.getInitializer(), Functions,
                             FunctionVisited);
            for (Function *Function : Functions)
                ExportedFunctions.insert(Function);
            SmallPtrSet<GlobalVariable *, 8> Globals;
            SmallPtrSet<Value *, 16> GlobalVisited;
            collectGlobals(GV.getInitializer(), Globals, GlobalVisited);
            for (GlobalVariable *Global : Globals)
                if (Global != &GV)
                    ExportedGlobals.insert(Global);
        }
    }

    void discoverSyscallEntryFunctions(Module &M) {
        for (GlobalVariable &GV : M.globals()) {
            if (!GV.hasInitializer())
                continue;
            StringRef Name = GV.getName();
#if LLVM_VERSION_MAJOR >= 18
            const bool IsSyscallTable =
                Name == "sys_call_table" ||
                Name.ends_with("_sys_call_table");
#else
            const bool IsSyscallTable =
                Name == "sys_call_table" ||
                Name.endswith("_sys_call_table");
#endif
            if (!IsSyscallTable)
                continue;
            SmallPtrSet<Function *, 32> Functions;
            SmallPtrSet<Value *, 32> Visited;
            collectFunctions(GV.getInitializer(), Functions, Visited);
            for (Function *Function : Functions)
                SyscallEntryFunctions.insert(Function);
        }
    }

    void emitNodes(Module &M) {
        for (Function &F : M) {
            if (F.isIntrinsic())
                continue;
            std::vector<std::string> Attributes;
            Attributes.push_back(F.isDeclarationForLinker() ? "declaration"
                                                            : "definition");
            if (F.hasAvailableExternallyLinkage())
                Attributes.push_back("available_externally");
            if (F.isWeakForLinker())
                Attributes.push_back("weak_for_linker");
            if (F.hasFnAttribute(Attribute::NoInline))
                Attributes.push_back("noinline");
            if (F.hasFnAttribute(Attribute::AlwaysInline))
                Attributes.push_back("__always_inline");
            if (F.hasFnAttribute(Attribute::InlineHint))
                Attributes.push_back("inline_hint");
            if (F.hasFnAttribute(Attribute::Naked))
                Attributes.push_back("naked");
            if (F.getSection() == ".init.text")
                Attributes.push_back("__init");
            if (ExportedFunctions.count(&F))
                Attributes.push_back("exported");
            if (SyscallEntryFunctions.count(&F))
                Attributes.push_back("syscall_entry");
            std::sort(Attributes.begin(), Attributes.end());

            const std::string Linkage =
                isInternal(F) ? "internal" : "external";
            const std::string Source = sourcePath(F);
            const std::string Section =
                F.hasSection() ? F.getSection().str() : "";
            Records.insert(
                "{\"attributes\":" + stringArray(Attributes) +
                ",\"contexts\":[],\"id\":" + jsonString(functionId(F)) +
                ",\"kind\":\"function\",\"linkage\":" +
                jsonString(Linkage) + ",\"record\":\"node\","
                "\"section\":" +
                optionalString(Section) +
                ",\"size_bytes\":null,\"source_path\":" +
                optionalString(Source) + ",\"symbol\":" +
                jsonString(F.getName()) + ",\"translation_unit\":" +
                (isInternal(F) ? jsonString(TranslationUnit) : "null") + "}");
        }

        for (GlobalVariable &GV : M.globals()) {
            std::vector<std::string> Attributes;
            StringRef Name = GV.getName();
            StringRef SectionName = GV.getSection();
#if LLVM_VERSION_MAJOR >= 18
            const bool IsStringLiteral =
                Name == ".str" || Name.starts_with(".str.");
#else
            const bool IsStringLiteral =
                Name == ".str" || Name.startswith(".str.");
#endif
            const bool IsCompilerMetadata =
                Name == "llvm.used" ||
                Name == "llvm.compiler.used" ||
                SectionName == "llvm.metadata";
            // Linux's __ADDRESSABLE() variables only retain a symbol for
            // assembler/linker references and are discarded from the final
            // image. They are not mutable runtime state to be moved into a
            // generated module.
            const bool IsRetentionMetadata =
                SectionName == ".discard.addressable";
            Attributes.push_back(
                (GV.isConstant() || IsCompilerMetadata ||
                 IsRetentionMetadata)
                    ? "immutable"
                    : "mutable");
            if (IsCompilerMetadata)
                Attributes.push_back("compiler_metadata");
            if (IsStringLiteral)
                Attributes.push_back("compiler_generated");
            if (IsRetentionMetadata)
                Attributes.push_back("retention_metadata");
            if (isInternal(GV))
                Attributes.push_back("internal");
            if (GV.isDeclaration())
                Attributes.push_back("declaration");
            else
                Attributes.push_back("definition");
            if (ExportedGlobals.count(&GV))
                Attributes.push_back("exported");
            if (GV.isWeakForLinker())
                Attributes.push_back("weak_for_linker");
            std::sort(Attributes.begin(), Attributes.end());
            const std::string Linkage =
                isInternal(GV) ? "internal" : "external";
            const std::string Section =
                GV.hasSection() ? GV.getSection().str() : "";
            Records.insert(
                "{\"attributes\":" + stringArray(Attributes) +
                ",\"contexts\":[],\"id\":" + jsonString(globalId(GV)) +
                ",\"kind\":\"global\",\"linkage\":" +
                jsonString(Linkage) + ",\"record\":\"node\","
                "\"section\":" +
                optionalString(Section) +
                ",\"size_bytes\":null,\"source_path\":" +
                (GV.isDeclaration() || GV.isWeakForLinker()
                     ? "null"
                     : jsonString(TranslationUnit)) +
                ",\"symbol\":" + jsonString(GV.getName()) +
                ",\"translation_unit\":" +
                (isInternal(GV) ? jsonString(TranslationUnit) : "null") + "}");
        }
    }

    std::string newConstantId(StringRef Kind) {
        return "const:" + TranslationUnit + ":" + Kind.str() + ":" +
               std::to_string(ConstantCounter++);
    }

    std::string ensurePointerValue(Value *V) {
        if (!V || !V->getType()->isPointerTy())
            return "";
        auto Existing = ValueIds.find(V);
        if (Existing != ValueIds.end())
            return Existing->second;

        if (auto *F = dyn_cast<Function>(V)) {
            // LLVM intrinsics are compiler IR operations, not kernel
            // functions.  They are declarations in the module but are
            // deliberately absent from the kernel reference graph.
            if (F->isIntrinsic())
                return "";
            const std::string Pointer = "ptr:" + functionId(*F);
            ValueIds[V] = Pointer;
            emitAddress(Pointer, functionId(*F));
            return Pointer;
        }
        if (auto *GV = dyn_cast<GlobalVariable>(V)) {
            const std::string Pointer = "ptr:" + globalId(*GV);
            ValueIds[V] = Pointer;
            emitAddress(Pointer, memoryCellObject(globalId(*GV)));
            return Pointer;
        }
        if (auto *Alias = dyn_cast<GlobalAlias>(V)) {
            const std::string Pointer = newConstantId("alias");
            ValueIds[V] = Pointer;
            if (Constant *Aliasee = Alias->getAliasee())
                emitCopy(Pointer, ensurePointerValue(Aliasee));
            return Pointer;
        }
        if (auto *Expression = dyn_cast<ConstantExpr>(V)) {
            const std::string Pointer = newConstantId("expr");
            ValueIds[V] = Pointer;
            if (Expression->isCast()) {
                emitCopy(Pointer,
                         ensurePointerValue(Expression->getOperand(0)));
            } else if (Expression->getOpcode() ==
                       Instruction::GetElementPtr) {
                emitGepAliases(
                    Pointer,
                    ensurePointerValue(Expression->getOperand(0)),
                    *Expression);
            }
            return Pointer;
        }
        const std::string Pointer = newConstantId("unknown");
        ValueIds[V] = Pointer;
        return Pointer;
    }

    static std::string typeKey(Type *ValueType) {
        if (!ValueType)
            return "unknown";
        std::string Text;
        raw_string_ostream Stream(Text);
        ValueType->print(Stream);
        Stream.flush();
        return Text;
    }

    static std::string qualifyFieldPath(Type *SourceType,
                                        StringRef Path) {
        return typeKey(SourceType) + "|" + Path.str();
    }

    static std::string abiTypeClass(Type *ValueType) {
        if (!ValueType)
            return "unknown";
        if (ValueType->isVoidTy())
            return "void";
        if (ValueType->isPointerTy())
            return "gpr";
        if (auto *Integer = dyn_cast<IntegerType>(ValueType))
            return Integer->getBitWidth() <= 64 ? "gpr" : "gpr-wide";
        if (ValueType->isFloatingPointTy())
            return "fp";
        if (ValueType->isVectorTy())
            return "vector";
        if (ValueType->isStructTy() || ValueType->isArrayTy())
            return "aggregate";
        return "other";
    }

    static std::string abiSignature(Type *ReturnType,
                                    ArrayRef<Type *> ArgumentTypes,
                                    bool IsVarArg, unsigned CallingConvention) {
        std::string Signature =
            "cc=" + std::to_string(CallingConvention) +
            ";ret=" + abiTypeClass(ReturnType) + ";args=";
        for (size_t Index = 0; Index < ArgumentTypes.size(); ++Index) {
            if (Index)
                Signature += ",";
            Signature += abiTypeClass(ArgumentTypes[Index]);
        }
        Signature += IsVarArg ? ";vararg=1" : ";vararg=0";
        return Signature;
    }

    static std::string functionSignature(const Function &F) {
        std::vector<Type *> Arguments;
        Arguments.reserve(F.arg_size());
        for (const Argument &Argument : F.args())
            Arguments.push_back(Argument.getType());
        return abiSignature(F.getReturnType(), Arguments,
                            F.isVarArg(), F.getCallingConv());
    }

    static std::string callSignature(const CallBase &Call) {
        std::vector<Type *> Arguments;
        Arguments.reserve(Call.arg_size());
        for (const Use &Argument : Call.args())
            Arguments.push_back(Argument->getType());
        return abiSignature(Call.getType(), Arguments, false,
                            Call.getCallingConv());
    }

    static std::pair<Type *, std::string> gepSourceAndPath(
        const User &Gep) {
        std::string Path;
        for (unsigned Index = 1; Index < Gep.getNumOperands(); ++Index) {
            if (Index == 1) {
                if (auto *RootIndex =
                        dyn_cast<ConstantInt>(Gep.getOperand(Index)))
                    if (RootIndex->isZero())
                        continue;
            }
            if (!Path.empty())
                Path += ".";
            if (auto *ConstantIndex =
                    dyn_cast<ConstantInt>(Gep.getOperand(Index)))
                Path += std::to_string(ConstantIndex->getSExtValue());
            else
                Path += "*";
        }
        if (Path.empty())
            Path = "*";
        const auto *Operator = dyn_cast<GEPOperator>(&Gep);
        return {
            Operator ? Operator->getSourceElementType() : nullptr,
            Path,
        };
    }

    bool layoutCoordinates(Type *SourceType, StringRef Path,
                           uint64_t &SourceBytes,
                           uint64_t &Offset) const {
        if (!Layout || !SourceType || !SourceType->isSized())
            return false;
        TypeSize SourceSize = Layout->getTypeAllocSize(SourceType);
        if (SourceSize.isScalable())
            return false;
        SourceBytes = SourceSize.getFixedValue();
        Offset = 0;
        Type *Current = SourceType;
        SmallVector<StringRef, 8> Components;
        Path.split(Components, '.');
        for (StringRef Component : Components) {
            if (Component == "*")
                return false;
            uint64_t Index = 0;
            if (Component.getAsInteger(10, Index))
                return false;
            if (auto *Struct = dyn_cast<StructType>(Current)) {
                if (Struct->isOpaque() ||
                    Index >= Struct->getNumElements())
                    return false;
                Offset +=
                    Layout->getStructLayout(Struct)->getElementOffset(Index);
                Current = Struct->getElementType(Index);
            } else if (auto *Array = dyn_cast<ArrayType>(Current)) {
                Type *Element = Array->getElementType();
                TypeSize ElementSize = Layout->getTypeAllocSize(Element);
                if (ElementSize.isScalable())
                    return false;
                Offset += Index * ElementSize.getFixedValue();
                Current = Element;
            } else {
                return false;
            }
        }
        return true;
    }

    static void hashLayoutValue(uint64_t &Hash, uint64_t Value) {
        for (unsigned Byte = 0; Byte < 8; ++Byte) {
            Hash ^= (Value >> (Byte * 8)) & 0xff;
            Hash *= 1099511628211ULL;
        }
    }

    bool hashLayoutLeaves(Type *ValueType, uint64_t BaseOffset,
                          uint64_t &Hash, unsigned &LeafCount) const {
        if (!ValueType || !ValueType->isSized())
            return false;
        if (ValueType->isPointerTy()) {
            hashLayoutValue(Hash, 'p');
            hashLayoutValue(Hash, BaseOffset);
            ++LeafCount;
            return LeafCount <= 512;
        }
        if (auto *Integer = dyn_cast<IntegerType>(ValueType)) {
            hashLayoutValue(Hash, 'i');
            hashLayoutValue(Hash, Integer->getBitWidth());
            hashLayoutValue(Hash, BaseOffset);
            ++LeafCount;
            return LeafCount <= 512;
        }
        if (ValueType->isFloatingPointTy()) {
            TypeSize Size = Layout->getTypeStoreSize(ValueType);
            if (Size.isScalable())
                return false;
            hashLayoutValue(Hash, 'f');
            hashLayoutValue(Hash, Size.getFixedValue());
            hashLayoutValue(Hash, BaseOffset);
            ++LeafCount;
            return LeafCount <= 512;
        }
        if (auto *Struct = dyn_cast<StructType>(ValueType)) {
            if (Struct->isOpaque())
                return false;
            const StructLayout *StructLayout =
                Layout->getStructLayout(Struct);
            for (unsigned Index = 0;
                 Index < Struct->getNumElements(); ++Index)
                if (!hashLayoutLeaves(
                        Struct->getElementType(Index),
                        BaseOffset +
                            StructLayout->getElementOffset(Index),
                        Hash, LeafCount))
                    return false;
            return true;
        }
        if (auto *Array = dyn_cast<ArrayType>(ValueType)) {
            Type *Element = Array->getElementType();
            // Clang materializes implicit aggregate padding as byte arrays
            // in some global initializer types.  Ignore those arrays so a
            // named runtime struct and its anonymous initializer literal
            // receive the same shape.  This is conservative for real byte
            // buffers but ABI filtering still constrains callback targets.
            if (Element->isIntegerTy(8))
                return true;
            if (Array->getNumElements() > 512)
                return false;
            TypeSize ElementSize = Layout->getTypeAllocSize(Element);
            if (ElementSize.isScalable())
                return false;
            for (uint64_t Index = 0;
                 Index < Array->getNumElements(); ++Index)
                if (!hashLayoutLeaves(
                        Element,
                        BaseOffset +
                            Index * ElementSize.getFixedValue(),
                        Hash, LeafCount))
                    return false;
            return true;
        }
        TypeSize Size = Layout->getTypeStoreSize(ValueType);
        if (Size.isScalable())
            return false;
        hashLayoutValue(Hash, 'o');
        hashLayoutValue(Hash, Size.getFixedValue());
        hashLayoutValue(Hash, BaseOffset);
        ++LeafCount;
        return LeafCount <= 512;
    }

    std::string layoutShape(Type *SourceType) const {
        if (!Layout || !SourceType || !SourceType->isSized())
            return "";
        uint64_t Hash = 1469598103934665603ULL;
        unsigned LeafCount = 0;
        if (!hashLayoutLeaves(SourceType, 0, Hash, LeafCount) ||
            LeafCount == 0)
            return "";
        std::ostringstream Stream;
        Stream << std::hex << Hash;
        return Stream.str();
    }

    std::string objectLayoutFieldPath(Type *SourceType, StringRef Path,
                                      StringRef ObjectScope) const {
        if (ObjectScope.empty())
            return "";
        uint64_t SourceBytes = 0;
        uint64_t Offset = 0;
        if (!layoutCoordinates(SourceType, Path, SourceBytes, Offset))
            return "";
        return "layout:" + ObjectScope.str() + "|" +
               std::to_string(SourceBytes) + "|" +
               std::to_string(Offset);
    }

    std::string shapeLayoutFieldPath(Type *SourceType,
                                     StringRef Path) const {
        uint64_t SourceBytes = 0;
        uint64_t Offset = 0;
        if (!layoutCoordinates(SourceType, Path, SourceBytes, Offset))
            return "";
        const std::string Shape = layoutShape(SourceType);
        if (Shape.empty())
            return "";
        return "layout-shape:" + Shape + "|" +
               std::to_string(SourceBytes) + "|" +
               std::to_string(Offset);
    }

    std::vector<std::string> fieldPathAliases(
        Type *SourceType, StringRef Path,
        StringRef ObjectScope = "") const {
        std::vector<std::string> Aliases = {
            qualifyFieldPath(SourceType, Path)
        };
        const std::string ObjectLayout =
            objectLayoutFieldPath(SourceType, Path, ObjectScope);
        if (!ObjectLayout.empty())
            Aliases.push_back(ObjectLayout);
        const std::string ShapeLayout =
            shapeLayoutFieldPath(SourceType, Path);
        if (!ShapeLayout.empty())
            Aliases.push_back(ShapeLayout);
        return Aliases;
    }

    static GlobalVariable *uniqueGlobalRoot(Value *ValueToInspect) {
        SmallPtrSet<Value *, 8> Visited;
        Value *Current = ValueToInspect;
        while (Current && Visited.insert(Current).second) {
            Value *Stripped = Current->stripPointerCasts();
            if (Stripped != Current) {
                Current = Stripped;
                continue;
            }
            if (auto *Global = dyn_cast<GlobalVariable>(Current))
                return Global;
            if (auto *Alias = dyn_cast<GlobalAlias>(Current)) {
                Current = Alias->getAliasee();
                continue;
            }
            if (auto *Gep = dyn_cast<GEPOperator>(Current)) {
                Current = Gep->getPointerOperand();
                continue;
            }
            if (auto *Expression = dyn_cast<ConstantExpr>(Current)) {
                if (Expression->isCast()) {
                    Current = Expression->getOperand(0);
                    continue;
                }
            }
            if (auto *Cast = dyn_cast<CastInst>(Current)) {
                Current = Cast->getOperand(0);
                continue;
            }
            return nullptr;
        }
        return nullptr;
    }

    static Function *uniqueEncodedFunctionBase(Value *ValueToInspect) {
        SmallPtrSet<Value *, 8> Visited;
        Value *Current = ValueToInspect;
        bool SawGep = false;
        while (Current && Visited.insert(Current).second) {
            if (auto *FunctionBase = dyn_cast<Function>(Current))
                return SawGep ? FunctionBase : nullptr;
            if (auto *Alias = dyn_cast<GlobalAlias>(Current)) {
                Current = Alias->getAliasee();
                continue;
            }
            if (auto *Gep = dyn_cast<GEPOperator>(Current)) {
                SawGep = true;
                Current = Gep->getPointerOperand();
                continue;
            }
            if (auto *Expression = dyn_cast<ConstantExpr>(Current)) {
                if (Expression->isCast()) {
                    Current = Expression->getOperand(0);
                    continue;
                }
            }
            if (auto *Cast = dyn_cast<CastInst>(Current)) {
                Current = Cast->getOperand(0);
                continue;
            }
            Value *Stripped = Current->stripPointerCasts();
            if (Stripped != Current) {
                Current = Stripped;
                continue;
            }
            return nullptr;
        }
        return nullptr;
    }

    void emitGepAliases(const std::string &Destination,
                        const std::string &Base, const User &Gep) {
        const auto SourceAndPath = gepSourceAndPath(Gep);
        GlobalVariable *Root = uniqueGlobalRoot(Gep.getOperand(0));
        const std::string ObjectScope =
            Root ? globalId(*Root) : "";
        for (const std::string &FieldPath :
             fieldPathAliases(SourceAndPath.first,
                              SourceAndPath.second,
                              ObjectScope))
            emitGep(Destination, Base, FieldPath);
    }

    void emitAddress(const std::string &Pointer, const std::string &Target) {
        if (Pointer.empty() || Target.empty())
            return;
        Records.insert("{\"pointer\":" + jsonString(Pointer) +
                       ",\"record\":\"address\",\"target\":" +
                       jsonString(Target) + "}");
    }

    void emitCopy(const std::string &Destination, const std::string &Source) {
        if (Destination.empty() || Source.empty())
            return;
        Records.insert("{\"destination\":" + jsonString(Destination) +
                       ",\"record\":\"copy\",\"source\":" +
                       jsonString(Source) + "}");
    }

    void emitLoad(const std::string &Destination,
                  const std::string &Pointer) {
        if (Destination.empty() || Pointer.empty())
            return;
        Records.insert("{\"destination\":" + jsonString(Destination) +
                       ",\"pointer\":" + jsonString(Pointer) +
                       ",\"record\":\"load\"}");
    }

    void emitStore(const std::string &Pointer, const std::string &Source) {
        if (Pointer.empty() || Source.empty())
            return;
        Records.insert("{\"pointer\":" + jsonString(Pointer) +
                       ",\"record\":\"store\",\"source\":" +
                       jsonString(Source) + "}");
    }

    void emitGep(const std::string &Destination, const std::string &Base,
                 const std::string &FieldPath) {
        if (Destination.empty() || Base.empty())
            return;
        Records.insert("{\"base\":" + jsonString(Base) +
                       ",\"destination\":" + jsonString(Destination) +
                       ",\"field_path\":" + jsonString(FieldPath) +
                       ",\"record\":\"gep\"}");
    }

    void emitEdge(const std::string &Source, const std::string &Target,
                  StringRef Kind, const std::string &FieldPath = "") {
        std::string Record =
            "{\"contexts\":[],\"evidence\":[\"llvm-reference-facts\"],"
            "\"field_path\":" +
            optionalString(FieldPath) + ",\"kind\":" + jsonString(Kind) +
            ",\"location\":null,\"record\":\"edge\",\"source\":" +
            jsonString(Source) + ",\"target\":" + jsonString(Target) + "}";
        Records.insert(Record);
    }

    void emitSummary(Function &F) {
        std::string Parameters = "[";
        unsigned Index = 0;
        for (Argument &Argument : F.args()) {
            if (Index)
                Parameters += ",";
            Parameters += Argument.getType()->isPointerTy()
                              ? jsonString(ensurePointerValue(&Argument))
                              : "null";
            ++Index;
        }
        Parameters += "]";
        const std::string Result =
            F.getReturnType()->isPointerTy() ? "ret:" + functionId(F) : "";
        Records.insert("{\"function\":" + jsonString(functionId(F)) +
                       ",\"parameters\":" + Parameters +
                       ",\"record\":\"summary\",\"result\":" +
                       optionalString(Result) + ",\"signature\":" +
                       jsonString(functionSignature(F)) + "}");
    }

    void emitCall(Function &Caller, CallBase &Call, unsigned CallIndex) {
        Value *Called = Call.getCalledOperand();
        Value *Stripped = Called->stripPointerCasts();
        // LLVM represents every inline-asm statement as a CallBase whose
        // callee is InlineAsm.  It is not an unresolved C function pointer.
        // Treating common barriers and arch helpers as indirect calls adds
        // tens of thousands of false UNKNOWN edges in a whole kernel.
        if (isa<InlineAsm>(Stripped))
            return;
        Function *Direct = dyn_cast<Function>(Stripped);
        if (Direct && Direct->isIntrinsic())
            return;
        Function *EncodedBase =
            Direct ? nullptr : uniqueEncodedFunctionBase(Called);
        const std::string CallId =
            "call:" + functionId(Caller) + ":tu:" + TranslationUnit + ":" +
            std::to_string(CallIndex);
        std::string Actuals = "[";
        for (unsigned Index = 0; Index < Call.arg_size(); ++Index) {
            if (Index)
                Actuals += ",";
            Value *Argument = Call.getArgOperand(Index);
            Actuals += Argument->getType()->isPointerTy()
                           ? jsonString(ensurePointerValue(Argument))
                           : "null";
        }
        Actuals += "]";
        const std::string Result = Call.getType()->isPointerTy()
                                       ? ensurePointerValue(&Call)
                                       : "";
        const std::string Location = debugLocation(Call);
        Records.insert(
            "{\"actuals\":" + Actuals + ",\"callee_pointer\":" +
            (Direct ? "null"
                    : jsonString(ensurePointerValue(Call.getCalledOperand()))) +
            ",\"caller\":" + jsonString(functionId(Caller)) +
            ",\"direct_target\":" +
            (Direct ? jsonString(functionId(*Direct)) : "null") +
            ",\"encoded_function_base\":" +
            (EncodedBase ? jsonString(functionId(*EncodedBase)) : "null") +
            ",\"id\":" + jsonString(CallId) +
            ",\"location\":" + optionalString(Location) +
            ",\"record\":\"call\",\"result\":" +
            optionalString(Result) + ",\"signature\":" +
            jsonString(callSignature(Call)) + "}");
    }

    std::string debugLocation(const Instruction &Instruction) const {
        const DebugLoc &Location = Instruction.getDebugLoc();
        if (!Location)
            return "";
        std::string File = Location->getFilename().str();
        if (!Location->getDirectory().empty())
            File = (Twine(Location->getDirectory()) + "/" +
                    Location->getFilename()).str();
        return normalizePath(File) + ":" +
               std::to_string(Location.getLine()) + ":" +
               std::to_string(Location.getCol());
    }

    void emitFunctionFacts(Module &M) {
        for (Function &F : M) {
            if (F.isIntrinsic())
                continue;
            emitSummary(F);
            if (F.isDeclarationForLinker())
                continue;
            const std::string FId = functionId(F);
            unsigned CallIndex = 0;
            for (BasicBlock &Block : F) {
                for (Instruction &Instruction : Block) {
                    emitInstructionConstraints(F, Instruction, CallIndex);
                    emitGlobalAndAddressReferences(FId, Instruction);
                }
            }
        }
    }

    void emitInstructionConstraints(Function &F, Instruction &Instruction,
                                    unsigned &CallIndex) {
        if (auto *Alloca = dyn_cast<AllocaInst>(&Instruction)) {
            const std::string Pointer = ensurePointerValue(Alloca);
            emitAddress(Pointer, "obj:stack:" + functionId(F) + ":" + Pointer);
        } else if (auto *Cast = dyn_cast<CastInst>(&Instruction)) {
            if (Cast->getType()->isPointerTy() &&
                Cast->getOperand(0)->getType()->isPointerTy())
                emitCopy(ensurePointerValue(Cast),
                         ensurePointerValue(Cast->getOperand(0)));
        } else if (auto *Phi = dyn_cast<PHINode>(&Instruction)) {
            if (Phi->getType()->isPointerTy())
                for (Value *Incoming : Phi->incoming_values())
                    emitCopy(ensurePointerValue(Phi),
                             ensurePointerValue(Incoming));
        } else if (auto *Select = dyn_cast<SelectInst>(&Instruction)) {
            if (Select->getType()->isPointerTy()) {
                emitCopy(ensurePointerValue(Select),
                         ensurePointerValue(Select->getTrueValue()));
                emitCopy(ensurePointerValue(Select),
                         ensurePointerValue(Select->getFalseValue()));
            }
        } else if (auto *Gep =
                       dyn_cast<GetElementPtrInst>(&Instruction)) {
            emitGepAliases(
                ensurePointerValue(Gep),
                ensurePointerValue(Gep->getPointerOperand()), *Gep);
        } else if (auto *Load = dyn_cast<LoadInst>(&Instruction)) {
            if (Load->getType()->isPointerTy())
                emitLoad(ensurePointerValue(Load),
                         ensurePointerValue(Load->getPointerOperand()));
        } else if (auto *Store = dyn_cast<StoreInst>(&Instruction)) {
            if (Store->getValueOperand()->getType()->isPointerTy())
                emitStore(ensurePointerValue(Store->getPointerOperand()),
                          ensurePointerValue(Store->getValueOperand()));
        }

        if (auto *Call = dyn_cast<CallBase>(&Instruction))
            emitCall(F, *Call, CallIndex++);
        if (auto *Return = dyn_cast<ReturnInst>(&Instruction)) {
            Value *Returned = Return->getReturnValue();
            if (Returned && Returned->getType()->isPointerTy())
                emitCopy("ret:" + functionId(F),
                         ensurePointerValue(Returned));
        }
    }

    void collectGlobals(Value *V,
                        SmallPtrSetImpl<GlobalVariable *> &Globals,
                        SmallPtrSetImpl<Value *> &Visited) {
        if (!V || !Visited.insert(V).second)
            return;
        if (auto *GV = dyn_cast<GlobalVariable>(V)) {
            Globals.insert(GV);
            return;
        }
        if (auto *UserValue = dyn_cast<User>(V))
            for (Value *Operand : UserValue->operand_values())
                collectGlobals(Operand, Globals, Visited);
    }

    void collectFunctions(Value *V, SmallPtrSetImpl<Function *> &Functions,
                          SmallPtrSetImpl<Value *> &Visited) {
        if (!V || !Visited.insert(V).second)
            return;
        if (auto *F = dyn_cast<Function>(V)) {
            if (!F->isIntrinsic())
                Functions.insert(F);
            return;
        }
        if (auto *UserValue = dyn_cast<User>(V))
            for (Value *Operand : UserValue->operand_values())
                collectFunctions(Operand, Functions, Visited);
    }

    void emitGlobalAndAddressReferences(const std::string &Caller,
                                        Instruction &Instruction) {
        SmallPtrSet<GlobalVariable *, 8> Globals;
        SmallPtrSet<Value *, 16> VisitedGlobals;
        for (Value *Operand : Instruction.operand_values())
            collectGlobals(Operand, Globals, VisitedGlobals);

        SmallPtrSet<GlobalVariable *, 4> WrittenGlobals;
        if (auto *Store = dyn_cast<StoreInst>(&Instruction)) {
            SmallPtrSet<Value *, 16> Visited;
            collectGlobals(Store->getPointerOperand(), WrittenGlobals,
                           Visited);
        }
        for (GlobalVariable *GV : Globals)
            emitEdge(Caller, globalId(*GV),
                     WrittenGlobals.count(GV) ? "global_write"
                                              : "global_read");

        SmallPtrSet<Function *, 8> Functions;
        SmallPtrSet<Value *, 16> VisitedFunctions;
        for (Value *Operand : Instruction.operand_values())
            collectFunctions(Operand, Functions, VisitedFunctions);
        Function *Direct = nullptr;
        if (auto *Call = dyn_cast<CallBase>(&Instruction))
            Direct =
                dyn_cast<Function>(Call->getCalledOperand()->stripPointerCasts());
        for (Function *Target : Functions)
            if (Target != Direct)
                emitEdge(Caller, functionId(*Target), "address_taken");
    }

    void emitGlobalInitializers(Module &M) {
        for (GlobalVariable &GV : M.globals()) {
            if (!GV.hasInitializer())
                continue;
            const std::string BasePointer = ensurePointerValue(&GV);
            emitInitializerValue(
                GV, GV.getInitializer(), BasePointer, {});
        }
    }

    void emitInitializerValue(GlobalVariable &Owner, Constant *InitValue,
                              const std::string &BasePointer,
                              const std::vector<std::pair<Type *, std::string>>
                                  &AggregatePaths) {
        if (!InitValue || isa<ConstantPointerNull>(InitValue) ||
            isa<UndefValue>(InitValue) || isa<PoisonValue>(InitValue))
            return;
        if (InitValue->getType()->isPointerTy()) {
            SmallPtrSet<Function *, 8> Functions;
            SmallPtrSet<Value *, 16> Visited;
            collectFunctions(InitValue, Functions, Visited);
            if (AggregatePaths.empty()) {
                emitStore(BasePointer, ensurePointerValue(InitValue));
                for (Function *Target : Functions)
                    emitEdge(globalId(Owner), functionId(*Target),
                             "global_initializer");
                return;
            }
            if (std::any_of(
                    AggregatePaths.begin(), AggregatePaths.end(),
                    [](const auto &AggregatePath) {
                        return isZeroFieldPath(AggregatePath.second);
                    })) {
                emitStore(BasePointer, ensurePointerValue(InitValue));
                for (Function *Target : Functions)
                    emitEdge(globalId(Owner), functionId(*Target),
                             "global_initializer");
            }
            // Emit one conservative memory-family alias for every enclosing
            // aggregate.  A global array initializer and a runtime access
            // through one struct element then share the struct-local family.
            // Array indices use '*' because runtime selection is commonly
            // dynamic.  Also emit every zero-offset prefix: LLVM commonly
            // folds ``outer.member.union_member`` into a GEP for
            // ``outer.member`` when the selected union/struct member is at
            // offset zero.  Those paths designate the same address.
            for (const auto &AggregatePath : AggregatePaths) {
                for (const std::string &Path :
                     zeroOffsetAliases(AggregatePath.second)) {
                    for (const std::string &QualifiedPath :
                         fieldPathAliases(AggregatePath.first, Path,
                                          globalId(Owner))) {
                        const std::string Destination =
                            "initfield:" + globalId(Owner) + ":" +
                            QualifiedPath;
                        emitGep(Destination, BasePointer, QualifiedPath);
                        emitStore(Destination,
                                  ensurePointerValue(InitValue));
                        for (Function *Target : Functions)
                            emitEdge(globalId(Owner),
                                     functionId(*Target),
                                     "global_initializer",
                                     QualifiedPath);
                    }
                }
            }
            return;
        }
        for (unsigned Index = 0; Index < InitValue->getNumOperands(); ++Index) {
            auto *Child = dyn_cast<Constant>(InitValue->getOperand(Index));
            if (!Child)
                continue;
            const bool IsSequential =
                InitValue->getType()->isArrayTy() ||
                InitValue->getType()->isVectorTy();
            const std::string Component =
                IsSequential ? "*" : std::to_string(Index);
            std::vector<std::pair<Type *, std::string>> ChildPaths;
            ChildPaths.reserve(AggregatePaths.size() + 1);
            for (const auto &AggregatePath : AggregatePaths)
                ChildPaths.emplace_back(
                    AggregatePath.first,
                    AggregatePath.second + "." + Component);
            ChildPaths.emplace_back(InitValue->getType(), Component);
            emitInitializerValue(
                Owner, Child, BasePointer, ChildPaths);
        }
    }

    static bool isZeroFieldPath(StringRef Path) {
        SmallVector<StringRef, 8> Components;
        Path.split(Components, '.');
        return !Components.empty() &&
               std::all_of(Components.begin(), Components.end(),
                           [](StringRef Component) {
                               return Component == "0";
                           });
    }

    static std::vector<std::string> zeroOffsetAliases(StringRef Path) {
        std::vector<std::string> Aliases;
        std::string Current = Path.str();
        while (!Current.empty()) {
            Aliases.push_back(Current);
            const size_t Separator = Current.rfind('.');
            if (Separator == std::string::npos ||
                Current.substr(Separator + 1) != "0")
                break;
            Current.resize(Separator);
        }
        return Aliases;
    }
};

class LegacyReferenceFacts : public ModulePass {
public:
    static char ID;

    LegacyReferenceFacts() : ModulePass(ID) {}

    bool runOnModule(Module &M) override {
        Emitter.run(M);
        return false;
    }

    void getAnalysisUsage(AnalysisUsage &AU) const override {
        AU.setPreservesAll();
    }

private:
    ReferenceFactsEmitter Emitter;
};

class NewReferenceFacts : public PassInfoMixin<NewReferenceFacts> {
public:
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
        Emitter.run(M);
        return PreservedAnalyses::all();
    }

private:
    ReferenceFactsEmitter Emitter;
};

} // namespace

char LegacyReferenceFacts::ID = 0;
static RegisterPass<LegacyReferenceFacts>
    X("reference-facts",
      "Emit linkage-aware Linux reference and pointer constraints", false,
      true);

extern "C" LLVM_ATTRIBUTE_WEAK PassPluginLibraryInfo
llvmGetPassPluginInfo() {
    return {
        LLVM_PLUGIN_API_VERSION,
        "ReferenceFacts",
        LLVM_VERSION_STRING,
        [](PassBuilder &Builder) {
            Builder.registerPipelineParsingCallback(
                [](StringRef Name, ModulePassManager &Manager,
                   ArrayRef<PassBuilder::PipelineElement>) {
                    if (Name != "reference-facts")
                        return false;
                    Manager.addPass(NewReferenceFacts());
                    return true;
                });
        },
    };
}
