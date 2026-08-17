// SPDX-License-Identifier: GPL-2.0
//
// Extract exact function source ranges and source-level dependencies with
// Clang's parser.  This deliberately replaces the old regular-expression
// backend: Linux declarations, attributes and macros are C syntax and must be
// located by a C parser.

#include "clang/AST/ASTContext.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendActions.h"
#include "clang/Lex/Lexer.h"
#include "clang/Lex/PPCallbacks.h"
#include "clang/Tooling/CommonOptionsParser.h"
#include "clang/Tooling/Tooling.h"
#include "llvm/ADT/StringSet.h"
#include "llvm/Config/llvm-config.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/Path.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <memory>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

using namespace clang;
using namespace clang::tooling;
using namespace llvm;

namespace {

cl::OptionCategory ExtractorCategory("kernel module source extractor");
cl::list<std::string> RequestedFunctions(
    "function", cl::desc("Function definition to extract (repeatable)"),
    cl::ZeroOrMore, cl::cat(ExtractorCategory));
cl::list<std::string> RequestedGlobals(
    "global", cl::desc("Global variable definition to extract (repeatable)"),
    cl::ZeroOrMore, cl::cat(ExtractorCategory));
cl::opt<bool> AllMainFunctions(
    "all-main-functions",
    cl::desc("Extract every non-macro function definition in the main file"),
    cl::init(false), cl::cat(ExtractorCategory));
cl::opt<bool> AllMainGlobals(
    "all-main-globals",
    cl::desc("Extract every global variable definition in the main file"),
    cl::init(false), cl::cat(ExtractorCategory));
cl::opt<std::string> OutputPath(
    "output", cl::desc("Output JSON path"), cl::value_desc("path"),
    cl::Required, cl::cat(ExtractorCategory));

struct MacroUse {
  uint64_t Offset = 0;
  std::string Name;
};

struct IncludeUse {
  uint64_t Offset = 0;
  std::string SourcePath;
  std::string Written;
  std::string Resolved;
  bool Angled = false;
};

struct MacroDefinitionFact {
  std::string Name;
  std::string SourcePath;
  uint64_t StartOffset = 0;
  uint64_t EndOffset = 0;
  std::string Source;
};

struct PreprocessorFacts {
  std::vector<MacroUse> Macros;
  std::vector<IncludeUse> Includes;
  std::vector<MacroDefinitionFact> MacroDefinitions;
};

struct DependencyFacts {
  std::set<std::string> Functions;
  std::set<std::string> Globals;
  std::set<std::string> Enums;
  std::set<std::string> Declarations;
  std::set<std::string> AddressTakenGlobals;
};

struct IdentifierReference {
  uint64_t Offset = 0;
  uint64_t Length = 0;
  std::string Kind;
  std::string Symbol;
};

struct SourceSliceFact {
  std::string SourcePath;
  uint64_t StartOffset = 0;
  uint64_t EndOffset = 0;
  std::string Source;
};

struct ExtractedFunction {
  std::string Symbol;
  std::string NameSpelling;
  std::string SourceForm = "function";
  std::string SourcePath;
  uint64_t StartOffset = 0;
  uint64_t EndOffset = 0;
  unsigned StartLine = 0;
  unsigned EndLine = 0;
  uint64_t NameOffset = 0;
  uint64_t NameLength = 0;
  uint64_t BodyStartOffset = 0;
  uint64_t BodyEndOffset = 0;
  std::string Source;
  std::string Signature;
  std::string ReturnType;
  std::string ReturnCategory;
  std::string Storage;
  bool Variadic = false;
  bool HasUnnamedParameter = false;
  std::vector<std::pair<std::string, std::string>> Parameters;
  std::vector<std::string> Attributes;
  std::vector<SourceSliceFact> PriorDeclarations;
  DependencyFacts Dependencies;
  std::vector<IdentifierReference> References;
  std::set<std::string> Macros;
};

struct ExtractedGlobal {
  std::string Symbol;
  std::string NameSpelling;
  std::string SourceForm = "global";
  std::string SourcePath;
  uint64_t StartOffset = 0;
  uint64_t EndOffset = 0;
  unsigned StartLine = 0;
  unsigned EndLine = 0;
  uint64_t NameOffset = 0;
  uint64_t NameLength = 0;
  std::string Source;
  std::string Type;
  std::string Storage;
  bool HasInitializer = false;
  std::vector<std::string> Attributes;
  DependencyFacts Dependencies;
  std::vector<IdentifierReference> References;
  std::set<std::string> Macros;
};

struct ExtractedDeclaration {
  std::string Kind;
  std::string Name;
  std::set<std::string> Identifiers;
  std::string SourcePath;
  uint64_t StartOffset = 0;
  uint64_t EndOffset = 0;
  unsigned StartLine = 0;
  unsigned EndLine = 0;
  std::string Source;
};

static std::string canonicalPath(StringRef Path) {
  SmallString<256> Result(Path);
  sys::fs::make_absolute(Result);
  sys::path::remove_dots(Result, true);
  return std::string(Result.str());
}

static std::string textForRange(const SourceManager &SM,
                                const LangOptions &Lang,
                                CharSourceRange Range) {
  bool Invalid = false;
  StringRef Text = Lexer::getSourceText(Range, SM, Lang, &Invalid);
  return Invalid ? std::string() : Text.str();
}

static bool fileOffsetsForRange(const SourceManager &SM,
                                const LangOptions &Lang, SourceRange Range,
                                uint64_t &Start, uint64_t &End) {
  SourceLocation Begin = SM.getExpansionLoc(Range.getBegin());
  SourceLocation Last = SM.getExpansionLoc(Range.getEnd());
  if (Begin.isInvalid() || Last.isInvalid() || !SM.isWrittenInMainFile(Begin) ||
      !SM.isWrittenInMainFile(Last))
    return false;
  SourceLocation After =
      Lexer::getLocForEndOfToken(Last, 0, SM, Lang);
  if (After.isInvalid() || !SM.isWrittenInMainFile(After))
    return false;
  Start = SM.getFileOffset(Begin);
  End = SM.getFileOffset(After);
  return End >= Start;
}

static std::string typeString(QualType Type, const ASTContext &Context) {
  PrintingPolicy Policy(Context.getLangOpts());
  Policy.SuppressScope = false;
  return Type.getAsString(Policy);
}

static std::string returnCategory(QualType Type) {
  Type = Type.getCanonicalType();
  if (Type->isVoidType())
    return "void";
  if (Type->isPointerType() || Type->isArrayType() ||
      Type->isFunctionPointerType() || Type->isMemberPointerType())
    return "pointer";
  if (Type->isBooleanType() || Type->isIntegerType() ||
      Type->isEnumeralType())
    return "integer";
  if (Type->isRealFloatingType())
    return "floating";
  if (Type->isRecordType())
    return "record";
  return "other";
}

static bool isIdentifierCharacter(char Character) {
  return std::isalnum(static_cast<unsigned char>(Character)) ||
         Character == '_';
}

static bool identifierOffset(StringRef Buffer, uint64_t Start, uint64_t End,
                             StringRef Identifier, uint64_t &Offset) {
  if (Start > End || End > Buffer.size() || Identifier.empty())
    return false;
  StringRef Window = Buffer.slice(Start, End);
  size_t Position = 0;
  unsigned Matches = 0;
  uint64_t MatchOffset = 0;
  while ((Position = Window.find(Identifier, Position)) != StringRef::npos) {
    const bool LeftBoundary =
        Position == 0 || !isIdentifierCharacter(Window[Position - 1]);
    const size_t After = Position + Identifier.size();
    const bool RightBoundary =
        After == Window.size() || !isIdentifierCharacter(Window[After]);
    if (LeftBoundary && RightBoundary) {
      ++Matches;
      MatchOffset = Start + Position;
    }
    Position += Identifier.size();
  }
  if (Matches != 1)
    return false;
  Offset = MatchOffset;
  return true;
}

static bool macroGeneratedIdentifierOffset(StringRef Buffer, uint64_t Start,
                                           uint64_t End, StringRef Symbol,
                                           uint64_t &Offset,
                                           std::string &Spelling) {
  if (Start > End || End > Buffer.size() || Symbol.empty())
    return false;

  // A token-pasting declaration such as DEVICE_ATTR_RO(type) has the AST
  // name ``dev_attr_type``, while only ``type`` is spelled at the expansion
  // site.  Select the longest source identifier that is a prefix or suffix
  // of the generated name.  Ties use the right-most token, which correctly
  // distinguishes the variable argument in forms such as
  // DEFINE_PER_CPU(struct state, state).
  StringRef Window = Buffer.slice(Start, End);
  size_t Position = 0;
  size_t BestLength = 0;
  uint64_t BestOffset = 0;
  std::string BestSpelling;
  while (Position < Window.size()) {
    if (!(std::isalpha(static_cast<unsigned char>(Window[Position])) ||
          Window[Position] == '_')) {
      ++Position;
      continue;
    }
    const size_t TokenStart = Position++;
    while (Position < Window.size() &&
           isIdentifierCharacter(Window[Position]))
      ++Position;
    StringRef Candidate = Window.slice(TokenStart, Position);
    if (Candidate.size() > Symbol.size())
      continue;
    const bool IsPrefix = Symbol.take_front(Candidate.size()) == Candidate;
    const bool IsSuffix = Symbol.take_back(Candidate.size()) == Candidate;
    if (!IsPrefix && !IsSuffix)
      continue;
    if (Candidate.size() < BestLength)
      continue;
    BestLength = Candidate.size();
    BestOffset = Start + TokenStart;
    BestSpelling = Candidate.str();
  }
  if (BestSpelling.empty())
    return false;
  Offset = BestOffset;
  Spelling = std::move(BestSpelling);
  return true;
}

static uint64_t findRawFollowingSemicolon(StringRef Buffer, uint64_t Start) {
  // ``VarDecl::getEndLoc()`` follows the active preprocessor branch.  A
  // declaration such as a concatenated string can then have an inactive
  // ``#if`` block between the last active token and its terminating
  // semicolon.  Lexer::findLocationAfterToken() intentionally does not jump
  // across those raw tokens.  Scan the original spelling as a fallback,
  // ignoring semicolons inside strings, character constants and comments.
  enum class State { Code, String, Character, LineComment, BlockComment };
  State Current = State::Code;
  bool Escaped = false;
  for (uint64_t Offset = Start; Offset < Buffer.size(); ++Offset) {
    const char Character = Buffer[Offset];
    const char Next =
        Offset + 1 < Buffer.size() ? Buffer[Offset + 1] : '\0';
    if (Current == State::LineComment) {
      if (Character == '\n')
        Current = State::Code;
      continue;
    }
    if (Current == State::BlockComment) {
      if (Character == '*' && Next == '/') {
        Current = State::Code;
        ++Offset;
      }
      continue;
    }
    if (Current == State::String || Current == State::Character) {
      if (Escaped) {
        Escaped = false;
        continue;
      }
      if (Character == '\\') {
        Escaped = true;
        continue;
      }
      if ((Current == State::String && Character == '"') ||
          (Current == State::Character && Character == '\''))
        Current = State::Code;
      continue;
    }
    if (Character == '/' && Next == '/') {
      Current = State::LineComment;
      ++Offset;
    } else if (Character == '/' && Next == '*') {
      Current = State::BlockComment;
      ++Offset;
    } else if (Character == '"') {
      Current = State::String;
    } else if (Character == '\'') {
      Current = State::Character;
    } else if (Character == ';') {
      return Offset + 1;
    }
  }
  return 0;
}

class RecordingPPCallbacks final : public PPCallbacks {
public:
  RecordingPPCallbacks(SourceManager &SM, const LangOptions &Lang,
                       PreprocessorFacts &Facts)
      : SM(SM), Lang(Lang), Facts(Facts) {}

  void MacroExpands(const Token &MacroNameTok, const MacroDefinition &,
                    SourceRange, const MacroArgs *) override {
    SourceLocation Loc = SM.getExpansionLoc(MacroNameTok.getLocation());
    if (!Loc.isValid() || !SM.isWrittenInMainFile(Loc))
      return;
    IdentifierInfo *Identifier = MacroNameTok.getIdentifierInfo();
    if (!Identifier)
      return;
    Facts.Macros.push_back(
        {SM.getFileOffset(Loc), Identifier->getName().str()});
  }

  void MacroDefined(const Token &MacroNameTok,
                    const MacroDirective *Directive) override {
    if (!Directive || !Directive->getMacroInfo())
      return;
    SourceLocation NameLoc =
        SM.getExpansionLoc(MacroNameTok.getLocation());
    SourceLocation EndLoc = SM.getExpansionLoc(
        Directive->getMacroInfo()->getDefinitionEndLoc());
    if (!NameLoc.isValid() || !EndLoc.isValid() ||
        !SM.isWrittenInMainFile(NameLoc) ||
        !SM.isWrittenInMainFile(EndLoc))
      return;
    IdentifierInfo *Identifier = MacroNameTok.getIdentifierInfo();
    if (!Identifier)
      return;

    StringRef Buffer = SM.getBufferData(SM.getMainFileID());
    uint64_t NameOffset = SM.getFileOffset(NameLoc);
    uint64_t Start = NameOffset;
    while (Start > 0 && Buffer[Start - 1] != '\n' &&
           Buffer[Start - 1] != '\r')
      --Start;
    SourceLocation After =
        Lexer::getLocForEndOfToken(EndLoc, 0, SM, Lang);
    if (!After.isValid() || !SM.isWrittenInMainFile(After))
      return;
    uint64_t End = SM.getFileOffset(After);
    if (End < Start || End > Buffer.size())
      return;
    StringRef Source = Buffer.slice(Start, End);
#if LLVM_VERSION_MAJOR >= 18
    if (!Source.ltrim().starts_with("#define"))
#else
    if (!Source.ltrim().startswith("#define"))
#endif
      return;
    Facts.MacroDefinitions.push_back(
        {Identifier->getName().str(), canonicalPath(SM.getFilename(NameLoc)),
         Start, End, Source.str()});
  }

#if LLVM_VERSION_MAJOR >= 16
  void InclusionDirective(SourceLocation HashLoc, const Token &, StringRef Name,
                          bool IsAngled, CharSourceRange,
                          OptionalFileEntryRef File, StringRef, StringRef,
                          const Module *, bool,
                          SrcMgr::CharacteristicKind) override {
#else
  void InclusionDirective(SourceLocation HashLoc, const Token &, StringRef Name,
                          bool IsAngled, CharSourceRange,
                          Optional<FileEntryRef> File, StringRef, StringRef,
                          const Module *, bool,
                          SrcMgr::CharacteristicKind) override {
#endif
    SourceLocation Loc = SM.getExpansionLoc(HashLoc);
    if (!Loc.isValid() || !SM.isWrittenInMainFile(Loc))
      return;
    std::string Resolved;
    if (File)
      Resolved = canonicalPath(File->getName());
    Facts.Includes.push_back(
        {SM.getFileOffset(Loc), canonicalPath(SM.getFilename(Loc)),
         Name.str(), Resolved, IsAngled});
  }

private:
  SourceManager &SM;
  const LangOptions &Lang;
  PreprocessorFacts &Facts;
};

class DependencyVisitor final
    : public RecursiveASTVisitor<DependencyVisitor> {
public:
  DependencyVisitor(DependencyFacts &Facts,
                    std::vector<IdentifierReference> &References,
                    const SourceManager &SM, const LangOptions &Lang,
                    uint64_t CoDefinitionStart = 0,
                    uint64_t CoDefinitionEnd = 0,
                    bool SkipMacroCoDefinitions = false)
      : Facts(Facts), References(References), SM(SM), Lang(Lang),
        CoDefinitionStart(CoDefinitionStart),
        CoDefinitionEnd(CoDefinitionEnd),
        SkipMacroCoDefinitions(SkipMacroCoDefinitions) {}

  bool VisitDeclRefExpr(DeclRefExpr *Expression) {
    const ValueDecl *Declaration = Expression->getDecl();
    if (SkipMacroCoDefinitions && Declaration->getLocation().isMacroID()) {
      SourceLocation CoDefinition =
          SM.getExpansionLoc(Declaration->getLocation());
      if (CoDefinition.isValid() && SM.isWrittenInMainFile(CoDefinition)) {
        const uint64_t DefinitionOffset = SM.getFileOffset(CoDefinition);
        if (DefinitionOffset >= CoDefinitionStart &&
            DefinitionOffset < CoDefinitionEnd)
          return true;
      }
    }
    IdentifierReference Reference;
    Reference.Symbol = Declaration->getNameAsString();
    Reference.Length = Reference.Symbol.size();

    // A DeclRefExpr inside a macro argument often has an expansion location
    // at the macro name (for example ``unlikely`` or ``WRITE_ONCE``).  That
    // location cannot be rewritten as the referenced identifier.  Ask Clang
    // to map the token range back to a file range first; for macro arguments
    // this recovers the user-written token while references originating in a
    // macro body or header inline correctly remain range-less.
    CharSourceRange FileRange = Lexer::makeFileCharRange(
        CharSourceRange::getTokenRange(Expression->getSourceRange()), SM,
        Lang);
    if (FileRange.isValid()) {
      SourceLocation Location = FileRange.getBegin();
      if (Location.isValid() && SM.isWrittenInMainFile(Location))
        Reference.Offset = SM.getFileOffset(Location);
    }
    if (const auto *Function = dyn_cast<FunctionDecl>(Declaration)) {
      Facts.Functions.insert(Function->getNameAsString());
      Reference.Kind = "function";
    } else if (const auto *Variable = dyn_cast<VarDecl>(Declaration)) {
      // A function-local static has global storage duration, but its
      // declaration and initializer are already part of the owning function
      // source slice and cannot be referenced outside that lexical scope.
      // Treating it as a translation-unit global creates a false closure node
      // (often optimized away in LLVM) and duplicates its definition.
      if (Variable->hasGlobalStorage() &&
          Variable->getDeclContext()->isTranslationUnit()) {
        Facts.Globals.insert(Variable->getNameAsString());
        Reference.Kind = "global";
      }
    } else if (const auto *Constant = dyn_cast<EnumConstantDecl>(Declaration)) {
      Facts.Enums.insert(Constant->getNameAsString());
      Reference.Kind = "enum";
    }
    if (!Reference.Kind.empty() && !Reference.Symbol.empty()) {
      StringRef Buffer = SM.getBufferData(SM.getMainFileID());
      if (Reference.Offset + Reference.Length <= Buffer.size() &&
          Buffer.slice(Reference.Offset,
                       Reference.Offset + Reference.Length) ==
              Reference.Symbol)
        References.push_back(std::move(Reference));
    }
    return true;
  }

  bool VisitMemberExpr(MemberExpr *Expression) {
    const auto *Field = dyn_cast<FieldDecl>(Expression->getMemberDecl());
    if (!Field)
      return true;
    const RecordDecl *Parent = Field->getParent();
    if (const RecordDecl *Definition = Parent->getDefinition())
      Parent = Definition;
    if (!Parent || Parent->getName().empty())
      return true;
    SourceLocation Location = SM.getExpansionLoc(Parent->getLocation());
    if (!Location.isValid() || !SM.isWrittenInMainFile(Location))
      return true;

    // The spelling of a moved function does not necessarily name every
    // private record whose layout it needs.  In ``value->child->field``, for
    // example, only Clang knows the record that owns ``field``.  Preserve
    // that semantic dependency so the backend can copy the file-local record
    // definition into the generated module.
    Facts.Declarations.insert(Parent->getNameAsString());
    return true;
  }

  bool VisitUnaryOperator(UnaryOperator *Expression) {
    if (Expression->getOpcode() != UO_AddrOf)
      return true;
    const Expr *Operand = Expression->getSubExpr()->IgnoreParenImpCasts();
    const auto *Reference = dyn_cast<DeclRefExpr>(Operand);
    if (!Reference)
      return true;
    const auto *Variable = dyn_cast<VarDecl>(Reference->getDecl());
    if (!Variable || !Variable->hasGlobalStorage() ||
        !Variable->getDeclContext()->isTranslationUnit())
      return true;
    if (SkipMacroCoDefinitions && Variable->getLocation().isMacroID()) {
      SourceLocation CoDefinition =
          SM.getExpansionLoc(Variable->getLocation());
      if (CoDefinition.isValid() && SM.isWrittenInMainFile(CoDefinition)) {
        const uint64_t DefinitionOffset = SM.getFileOffset(CoDefinition);
        if (DefinitionOffset >= CoDefinitionStart &&
            DefinitionOffset < CoDefinitionEnd)
          return true;
      }
    }
    Facts.AddressTakenGlobals.insert(Variable->getNameAsString());
    return true;
  }

private:
  DependencyFacts &Facts;
  std::vector<IdentifierReference> &References;
  const SourceManager &SM;
  const LangOptions &Lang;
  uint64_t CoDefinitionStart;
  uint64_t CoDefinitionEnd;
  bool SkipMacroCoDefinitions;
};

class ExtractorVisitor final : public RecursiveASTVisitor<ExtractorVisitor> {
public:
  ExtractorVisitor(ASTContext &Context,
                   const StringSet<> &RequestedFunctions,
                   const StringSet<> &RequestedGlobals,
                   const PreprocessorFacts &PP,
                   std::vector<ExtractedFunction> &FunctionOutput,
                   std::vector<ExtractedGlobal> &GlobalOutput,
                   std::vector<ExtractedDeclaration> &DeclarationOutput)
      : Context(Context), SM(Context.getSourceManager()),
        RequestedFunctions(RequestedFunctions),
        RequestedGlobals(RequestedGlobals), PP(PP),
        FunctionOutput(FunctionOutput), GlobalOutput(GlobalOutput),
        DeclarationOutput(DeclarationOutput) {}

  bool VisitFunctionDecl(FunctionDecl *Function) {
    const std::string ASTSymbol = Function->getNameAsString();
    // getBody() follows the redeclaration chain and can return a later body
    // even when the currently visited declaration is only a prototype. Use
    // declaration-local definition identity before asking for the body.
    if (!Function->isThisDeclarationADefinition()) {
      recordPriorFunctionDeclaration(Function, ASTSymbol);
      return true;
    }
    const Stmt *Body = Function->getBody();
    if (!Body)
      return true;
    std::string OutputSymbol = ASTSymbol;
    std::string SourceForm = "function";
    bool Requested = RequestedFunctions.contains(Function->getName());
    const SourceLocation FunctionLocation =
        SM.getExpansionLoc(Function->getLocation());
    const bool DiscoverAll =
        AllMainFunctions && FunctionLocation.isValid() &&
        SM.isWrittenInMainFile(FunctionLocation) &&
        !Function->getLocation().isMacroID();
    StringRef ASTName(ASTSymbol);
#if LLVM_VERSION_MAJOR >= 18
    const bool IsGeneratedSyscallEntry =
        ASTName.starts_with("__se_sys_") ||
        ASTName.starts_with("__se_compat_sys_");
#else
    const bool IsGeneratedSyscallEntry =
        ASTName.startswith("__se_sys_") ||
        ASTName.startswith("__se_compat_sys_");
#endif
    // SYSCALL_DEFINE emits an intermediate __se_sys_* definition whose
    // spelling range is entirely inside macro replacement text.  The paired
    // __do_sys_* declaration below owns the user-written signature and body.
    if (Requested && IsGeneratedSyscallEntry &&
        Function->getLocation().isMacroID())
      return true;
    if (!Requested) {
      StringRef Name(ASTSymbol);
#if LLVM_VERSION_MAJOR >= 18
      const bool IsSyscallImplementation =
          Name.starts_with("__do_sys_") ||
          Name.starts_with("__do_compat_sys_");
#else
      const bool IsSyscallImplementation =
          Name.startswith("__do_sys_") ||
          Name.startswith("__do_compat_sys_");
#endif
      if (IsSyscallImplementation) {
        const bool IsCompat =
#if LLVM_VERSION_MAJOR >= 18
            Name.starts_with("__do_compat_sys_");
#else
            Name.startswith("__do_compat_sys_");
#endif
        std::string Interface = IsCompat ? "__se_compat_sys_" : "__se_sys_";
        Interface += Name.drop_front(IsCompat ? 16 : 9).str();
        if (RequestedFunctions.contains(Interface)) {
          Requested = true;
          OutputSymbol = Interface;
          SourceForm = "syscall_define";
        }
      }
    }
    if (!Requested && DiscoverAll)
      Requested = true;
    if (!Requested)
      return true;

    SourceLocation Begin = SM.getExpansionLoc(Function->getBeginLoc());
    SourceLocation End = SM.getExpansionLoc(Body->getEndLoc());
    if (!Begin.isValid() || !End.isValid() || !SM.isWrittenInMainFile(Begin) ||
        !SM.isWrittenInMainFile(End))
      return true;

    ExtractedFunction Record;
    if (!fileOffsetsForRange(SM, Context.getLangOpts(),
                             SourceRange(Begin, End), Record.StartOffset,
                             Record.EndOffset))
      return true;

    uint64_t BodyStart = 0;
    uint64_t BodyEnd = 0;
    if (!fileOffsetsForRange(SM, Context.getLangOpts(), Body->getSourceRange(),
                             BodyStart, BodyEnd))
      return true;

    Record.Symbol = OutputSymbol;
    Record.NameSpelling = OutputSymbol;
    Record.SourceForm = SourceForm;
    Record.SourcePath = canonicalPath(SM.getFilename(Begin));
    Record.StartLine = SM.getSpellingLineNumber(Begin);
    SourceLocation LastByte = End;
    Record.EndLine = SM.getSpellingLineNumber(LastByte);
    Record.BodyStartOffset = BodyStart;
    Record.BodyEndOffset = BodyEnd;
    Record.NameOffset =
        SM.getFileOffset(SM.getExpansionLoc(Function->getLocation()));
    Record.NameLength = Record.Symbol.size();

    FileID Main = SM.getMainFileID();
    StringRef Buffer = SM.getBufferData(Main);
    if (Record.EndOffset > Buffer.size() ||
        Record.StartOffset > Record.EndOffset ||
        BodyStart < Record.StartOffset || BodyStart > Record.EndOffset)
      return true;
    Record.Source =
        Buffer.slice(Record.StartOffset, Record.EndOffset).str();
    auto Prior = PriorFunctionDeclarations.find(ASTSymbol);
    if (Prior != PriorFunctionDeclarations.end()) {
      for (const SourceSliceFact &Declaration : Prior->second) {
        if (Declaration.EndOffset <= Record.StartOffset)
          Record.PriorDeclarations.push_back(Declaration);
      }
    }
    Record.Signature =
        Buffer.slice(Record.StartOffset, BodyStart).trim().str();
    if (SourceForm == "syscall_define") {
      StringRef Interface(OutputSymbol);
      const bool IsCompat =
#if LLVM_VERSION_MAJOR >= 18
          Interface.starts_with("__se_compat_sys_");
#else
          Interface.startswith("__se_compat_sys_");
#endif
      const StringRef Spelling = Interface.drop_front(IsCompat ? 16 : 9);
      if (!identifierOffset(Buffer, Record.StartOffset, BodyStart,
                            Spelling, Record.NameOffset))
        return true;
      Record.NameSpelling = Spelling.str();
      Record.NameLength = Spelling.size();
    }
    Record.ReturnType = typeString(Function->getReturnType(), Context);
    Record.ReturnCategory = returnCategory(Function->getReturnType());
    Record.Variadic = Function->isVariadic();

    switch (Function->getStorageClass()) {
    case SC_Static:
      Record.Storage = "static";
      break;
    case SC_Extern:
      Record.Storage = "extern";
      break;
    default:
      Record.Storage = "none";
      break;
    }

    for (const ParmVarDecl *Parameter : Function->parameters()) {
      std::string Name = Parameter->getNameAsString();
      if (Name.empty())
        Record.HasUnnamedParameter = true;
      Record.Parameters.emplace_back(
          std::move(Name), typeString(Parameter->getType(), Context));
    }
    for (const Attr *Attribute : Function->attrs()) {
      StringRef Spelling = Attribute->getSpelling();
      if (!Spelling.empty())
        Record.Attributes.push_back(Spelling.str());
    }
    if (Function->isInlineSpecified())
      Record.Attributes.push_back("inline");

    DependencyVisitor DependencyCollector(
        Record.Dependencies, Record.References, SM,
        Context.getLangOpts());
    DependencyCollector.TraverseStmt(const_cast<Stmt *>(Body));
    Record.Dependencies.Functions.erase(Record.Symbol);
    Record.Dependencies.Functions.erase(ASTSymbol);

    for (const MacroUse &Macro : PP.Macros) {
      if (Macro.Offset >= Record.StartOffset &&
          Macro.Offset < Record.EndOffset)
        Record.Macros.insert(Macro.Name);
    }

    FunctionOutput.push_back(std::move(Record));
    return true;
  }

  bool VisitVarDecl(VarDecl *Variable) {
    if (!Variable->hasGlobalStorage() ||
        !Variable->getDeclContext()->isTranslationUnit() ||
        Variable->isThisDeclarationADefinition() == VarDecl::DeclarationOnly)
      return true;

    SourceLocation Begin = SM.getExpansionLoc(Variable->getBeginLoc());
    SourceLocation End = SM.getExpansionLoc(Variable->getEndLoc());
    const bool IsMacroGenerated = Variable->getLocation().isMacroID();
    if (IsMacroGenerated) {
      CharSourceRange Expansion =
          SM.getExpansionRange(Variable->getLocation());
      if (Expansion.isValid()) {
        SourceLocation ExpansionBegin =
            SM.getExpansionLoc(Expansion.getBegin());
        SourceLocation ExpansionEnd = SM.getExpansionLoc(Expansion.getEnd());
        if (ExpansionBegin.isValid() &&
            SM.isWrittenInMainFile(ExpansionBegin) &&
            (!Begin.isValid() || !SM.isWrittenInMainFile(Begin) ||
             SM.getFileOffset(ExpansionBegin) < SM.getFileOffset(Begin)))
          Begin = ExpansionBegin;
        if (ExpansionEnd.isValid() && SM.isWrittenInMainFile(ExpansionEnd) &&
            (!End.isValid() || !SM.isWrittenInMainFile(End) ||
             SM.getFileOffset(ExpansionEnd) > SM.getFileOffset(End)))
          End = ExpansionEnd;
      }
    }
    if (!Begin.isValid() || !End.isValid() || !SM.isWrittenInMainFile(Begin) ||
        !SM.isWrittenInMainFile(End))
      return true;
    const bool ExplicitlyRequested =
        RequestedGlobals.contains(Variable->getName());
    if (!ExplicitlyRequested && !AllMainGlobals)
      return true;
    if (!ExplicitlyRequested && Variable->getLocation().isMacroID())
      return true;

    ExtractedGlobal Record;
    if (!fileOffsetsForRange(SM, Context.getLangOpts(),
                             SourceRange(Begin, End), Record.StartOffset,
                             Record.EndOffset))
      return true;

    SourceLocation AfterSemi = Lexer::findLocationAfterToken(
        End, tok::semi, SM, Context.getLangOpts(),
        /*SkipTrailingWhitespaceAndNewLine=*/false);
    if (AfterSemi.isValid() && SM.isWrittenInMainFile(AfterSemi))
      Record.EndOffset = SM.getFileOffset(AfterSemi);

    StringRef Buffer = SM.getBufferData(SM.getMainFileID());
    if (!AfterSemi.isValid() || !SM.isWrittenInMainFile(AfterSemi)) {
      const uint64_t RawEnd =
          findRawFollowingSemicolon(Buffer, Record.EndOffset);
      if (RawEnd != 0)
        Record.EndOffset = RawEnd;
    }
    if (Record.EndOffset > Buffer.size() ||
        Record.StartOffset > Record.EndOffset)
      return true;

    Record.Symbol = Variable->getNameAsString();
    Record.NameSpelling = Record.Symbol;
    if (IsMacroGenerated)
      Record.SourceForm = "macro_declaration_group";
    Record.SourcePath = canonicalPath(SM.getFilename(Begin));
    Record.StartLine = SM.getSpellingLineNumber(Begin);
    Record.EndLine = SM.getLineNumber(
        SM.getMainFileID(),
        Record.EndOffset == 0 ? 0 : Record.EndOffset - 1);
    if (!identifierOffset(Buffer, Record.StartOffset, Record.EndOffset,
                          Record.Symbol, Record.NameOffset)) {
      if (!IsMacroGenerated ||
          !macroGeneratedIdentifierOffset(
              Buffer, Record.StartOffset, Record.EndOffset, Record.Symbol,
              Record.NameOffset, Record.NameSpelling))
        return true;
    }
    Record.NameLength = Record.NameSpelling.size();
    Record.Source =
        Buffer.slice(Record.StartOffset, Record.EndOffset).str();
    Record.Type = typeString(Variable->getType(), Context);
    Record.HasInitializer = Variable->hasInit();
    switch (Variable->getStorageClass()) {
    case SC_Static:
      Record.Storage = "static";
      break;
    case SC_Extern:
      Record.Storage = "extern";
      break;
    default:
      Record.Storage = "none";
      break;
    }
    for (const Attr *Attribute : Variable->attrs()) {
      StringRef Spelling = Attribute->getSpelling();
      if (!Spelling.empty())
        Record.Attributes.push_back(Spelling.str());
    }

    if (const Expr *Initializer = Variable->getInit()) {
      DependencyVisitor DependencyCollector(
          Record.Dependencies, Record.References, SM,
          Context.getLangOpts(), Record.StartOffset, Record.EndOffset,
          IsMacroGenerated);
      DependencyCollector.TraverseStmt(const_cast<Expr *>(Initializer));
    }
    for (const MacroUse &Macro : PP.Macros) {
      if (Macro.Offset >= Record.StartOffset &&
          Macro.Offset < Record.EndOffset)
        Record.Macros.insert(Macro.Name);
    }
    GlobalOutput.push_back(std::move(Record));
    return true;
  }

  bool VisitTypedefDecl(TypedefDecl *Declaration) {
    if (!Declaration->getDeclContext()->isTranslationUnit())
      return true;
    std::set<std::string> Identifiers{Declaration->getNameAsString()};
    recordDeclaration(Declaration, "typedef",
                      Declaration->getNameAsString(), Identifiers);
    return true;
  }

  bool VisitEnumDecl(EnumDecl *Declaration) {
    if (!Declaration->isCompleteDefinition() ||
        !Declaration->getDeclContext()->isTranslationUnit())
      return true;
    std::set<std::string> Identifiers;
    std::string Name = Declaration->getNameAsString();
    if (!Name.empty())
      Identifiers.insert(Name);
    for (const EnumConstantDecl *Constant : Declaration->enumerators())
      Identifiers.insert(Constant->getNameAsString());
    recordDeclaration(Declaration, "enum", Name, Identifiers);
    return true;
  }

  bool VisitRecordDecl(RecordDecl *Declaration) {
    if (!Declaration->getDeclContext()->isTranslationUnit() ||
        Declaration->getName().empty())
      return true;
    // Keep explicit file-scope forward declarations as well as complete
    // definitions.  A tag first mentioned inside a function typedef or
    // prototype has prototype scope in C; dropping an earlier
    // ``struct tag;`` declaration can therefore make otherwise identical
    // function-pointer types incompatible in the generated module.
    std::set<std::string> Identifiers{Declaration->getNameAsString()};
    recordDeclaration(Declaration, "record",
                      Declaration->getNameAsString(), Identifiers);
    return true;
  }

private:
  void recordPriorFunctionDeclaration(const FunctionDecl *Declaration,
                                      StringRef Symbol) {
    if (!Declaration->getDeclContext()->isTranslationUnit() ||
        Declaration->getLocation().isMacroID())
      return;
    SourceLocation Begin =
        SM.getExpansionLoc(Declaration->getBeginLoc());
    SourceLocation End = SM.getExpansionLoc(Declaration->getEndLoc());
    if (!Begin.isValid() || !End.isValid() ||
        !SM.isWrittenInMainFile(Begin) || !SM.isWrittenInMainFile(End))
      return;

    SourceSliceFact Record;
    if (!fileOffsetsForRange(SM, Context.getLangOpts(),
                             SourceRange(Begin, End), Record.StartOffset,
                             Record.EndOffset))
      return;
    SourceLocation AfterSemi = Lexer::findLocationAfterToken(
        End, tok::semi, SM, Context.getLangOpts(),
        /*SkipTrailingWhitespaceAndNewLine=*/false);
    if (!AfterSemi.isValid() || !SM.isWrittenInMainFile(AfterSemi))
      return;
    Record.EndOffset = SM.getFileOffset(AfterSemi);
    StringRef Buffer = SM.getBufferData(SM.getMainFileID());
    if (Record.EndOffset > Buffer.size() ||
        Record.StartOffset >= Record.EndOffset)
      return;
    Record.SourcePath = canonicalPath(SM.getFilename(Begin));
    Record.Source =
        Buffer.slice(Record.StartOffset, Record.EndOffset).str();
    auto &Declarations = PriorFunctionDeclarations[Symbol.str()];
    if (std::none_of(
            Declarations.begin(), Declarations.end(),
            [&](const SourceSliceFact &Existing) {
              return Existing.StartOffset == Record.StartOffset &&
                     Existing.EndOffset == Record.EndOffset;
            }))
      Declarations.push_back(std::move(Record));
  }

  void recordDeclaration(const Decl *Declaration, StringRef Kind,
                         StringRef Name,
                         const std::set<std::string> &Identifiers) {
    SourceLocation Begin =
        SM.getExpansionLoc(Declaration->getBeginLoc());
    SourceLocation End = SM.getExpansionLoc(Declaration->getEndLoc());
    if (!Begin.isValid() || !End.isValid() ||
        !SM.isWrittenInMainFile(Begin) ||
        !SM.isWrittenInMainFile(End) ||
        Declaration->getLocation().isMacroID())
      return;

    ExtractedDeclaration Record;
    if (!fileOffsetsForRange(SM, Context.getLangOpts(),
                             SourceRange(Begin, End), Record.StartOffset,
                             Record.EndOffset))
      return;
    SourceLocation AfterSemi = Lexer::findLocationAfterToken(
        End, tok::semi, SM, Context.getLangOpts(),
        /*SkipTrailingWhitespaceAndNewLine=*/false);
    if (AfterSemi.isValid() && SM.isWrittenInMainFile(AfterSemi))
      Record.EndOffset = SM.getFileOffset(AfterSemi);
    StringRef Buffer = SM.getBufferData(SM.getMainFileID());
    if (Record.EndOffset > Buffer.size() ||
        Record.StartOffset > Record.EndOffset)
      return;

    Record.Kind = Kind.str();
    Record.Name = Name.str();
    if (Record.Name.empty())
      Record.Name = Kind.str() + "@" + std::to_string(Record.StartOffset);
    Record.Identifiers = Identifiers;
    Record.SourcePath = canonicalPath(SM.getFilename(Begin));
    Record.StartLine = SM.getSpellingLineNumber(Begin);
    Record.EndLine = SM.getSpellingLineNumber(End);
    Record.Source =
        Buffer.slice(Record.StartOffset, Record.EndOffset).str();
    DeclarationOutput.push_back(std::move(Record));
  }

  ASTContext &Context;
  SourceManager &SM;
  const StringSet<> &RequestedFunctions;
  const StringSet<> &RequestedGlobals;
  const PreprocessorFacts &PP;
  std::vector<ExtractedFunction> &FunctionOutput;
  std::vector<ExtractedGlobal> &GlobalOutput;
  std::vector<ExtractedDeclaration> &DeclarationOutput;
  std::map<std::string, std::vector<SourceSliceFact>>
      PriorFunctionDeclarations;
};

class ExtractorConsumer final : public ASTConsumer {
public:
  ExtractorConsumer(const StringSet<> &RequestedFunctions,
                    const StringSet<> &RequestedGlobals,
                    const PreprocessorFacts &PP,
                    std::vector<ExtractedFunction> &FunctionOutput,
                    std::vector<ExtractedGlobal> &GlobalOutput,
                    std::vector<ExtractedDeclaration> &DeclarationOutput)
      : RequestedFunctions(RequestedFunctions),
        RequestedGlobals(RequestedGlobals), PP(PP),
        FunctionOutput(FunctionOutput), GlobalOutput(GlobalOutput),
        DeclarationOutput(DeclarationOutput) {}

  void HandleTranslationUnit(ASTContext &Context) override {
    ExtractorVisitor Visitor(Context, RequestedFunctions, RequestedGlobals, PP,
                             FunctionOutput, GlobalOutput,
                             DeclarationOutput);
    Visitor.TraverseDecl(Context.getTranslationUnitDecl());
  }

private:
  const StringSet<> &RequestedFunctions;
  const StringSet<> &RequestedGlobals;
  const PreprocessorFacts &PP;
  std::vector<ExtractedFunction> &FunctionOutput;
  std::vector<ExtractedGlobal> &GlobalOutput;
  std::vector<ExtractedDeclaration> &DeclarationOutput;
};

class ExtractorAction final : public ASTFrontendAction {
public:
  ExtractorAction(const StringSet<> &RequestedFunctions,
                  const StringSet<> &RequestedGlobals,
                  std::vector<ExtractedFunction> &FunctionOutput,
                  std::vector<ExtractedGlobal> &GlobalOutput,
                  std::vector<ExtractedDeclaration> &DeclarationOutput,
                  std::vector<IncludeUse> &Includes,
                  std::vector<MacroDefinitionFact> &MacroDefinitions,
                  bool &HadDiagnosticError)
      : RequestedFunctions(RequestedFunctions),
        RequestedGlobals(RequestedGlobals), FunctionOutput(FunctionOutput),
        GlobalOutput(GlobalOutput), DeclarationOutput(DeclarationOutput),
        Includes(Includes), MacroDefinitions(MacroDefinitions),
        HadDiagnosticError(HadDiagnosticError) {}

  bool BeginSourceFileAction(CompilerInstance &Compiler) override {
    Facts = std::make_shared<PreprocessorFacts>();
    Compiler.getPreprocessor().addPPCallbacks(
        std::make_unique<RecordingPPCallbacks>(
            Compiler.getSourceManager(), Compiler.getLangOpts(), *Facts));
    return true;
  }

  std::unique_ptr<ASTConsumer>
  CreateASTConsumer(CompilerInstance &, StringRef) override {
    return std::make_unique<ExtractorConsumer>(
        RequestedFunctions, RequestedGlobals, *Facts, FunctionOutput,
        GlobalOutput, DeclarationOutput);
  }

  void EndSourceFileAction() override {
    Includes.insert(Includes.end(), Facts->Includes.begin(),
                    Facts->Includes.end());
    MacroDefinitions.insert(MacroDefinitions.end(),
                            Facts->MacroDefinitions.begin(),
                            Facts->MacroDefinitions.end());
    HadDiagnosticError |=
        getCompilerInstance().getDiagnostics().hasErrorOccurred();
  }

private:
  const StringSet<> &RequestedFunctions;
  const StringSet<> &RequestedGlobals;
  std::vector<ExtractedFunction> &FunctionOutput;
  std::vector<ExtractedGlobal> &GlobalOutput;
  std::vector<ExtractedDeclaration> &DeclarationOutput;
  std::vector<IncludeUse> &Includes;
  std::vector<MacroDefinitionFact> &MacroDefinitions;
  bool &HadDiagnosticError;
  std::shared_ptr<PreprocessorFacts> Facts;
};

class ExtractorActionFactory final : public FrontendActionFactory {
public:
  ExtractorActionFactory(const StringSet<> &RequestedFunctions,
                         const StringSet<> &RequestedGlobals,
                         std::vector<ExtractedFunction> &FunctionOutput,
                         std::vector<ExtractedGlobal> &GlobalOutput,
                         std::vector<ExtractedDeclaration> &DeclarationOutput,
                         std::vector<IncludeUse> &Includes,
                         std::vector<MacroDefinitionFact> &MacroDefinitions,
                         bool &HadDiagnosticError)
      : RequestedFunctions(RequestedFunctions),
        RequestedGlobals(RequestedGlobals), FunctionOutput(FunctionOutput),
        GlobalOutput(GlobalOutput), DeclarationOutput(DeclarationOutput),
        Includes(Includes), MacroDefinitions(MacroDefinitions),
        HadDiagnosticError(HadDiagnosticError) {}

  std::unique_ptr<FrontendAction> create() override {
    return std::make_unique<ExtractorAction>(
        RequestedFunctions, RequestedGlobals, FunctionOutput, GlobalOutput,
        DeclarationOutput, Includes, MacroDefinitions, HadDiagnosticError);
  }

private:
  const StringSet<> &RequestedFunctions;
  const StringSet<> &RequestedGlobals;
  std::vector<ExtractedFunction> &FunctionOutput;
  std::vector<ExtractedGlobal> &GlobalOutput;
  std::vector<ExtractedDeclaration> &DeclarationOutput;
  std::vector<IncludeUse> &Includes;
  std::vector<MacroDefinitionFact> &MacroDefinitions;
  bool &HadDiagnosticError;
};

static CommandLineArguments
stripGCCOnlyKernelArguments(const CommandLineArguments &Arguments,
                            StringRef) {
  CommandLineArguments Result;
  for (const std::string &Argument : Arguments) {
    StringRef Value(Argument);
    if (Value.starts_with("-mpreferred-stack-boundary=") ||
        Value.starts_with("-mindirect-branch=") ||
        Value == "-mindirect-branch-register" ||
        Value == "-mindirect-branch-cs-prefix" ||
        Value.starts_with("-mfunction-return=") ||
        Value == "-fno-allow-store-data-races" ||
        Value == "-fconserve-stack")
      continue;
    Result.push_back(Argument);
  }
  return Result;
}

static json::Array stringsToJSON(const std::set<std::string> &Values) {
  json::Array Result;
  for (const std::string &Value : Values)
    Result.push_back(Value);
  return Result;
}

static json::Array
referencesToJSON(const std::vector<IdentifierReference> &References) {
  json::Array Result;
  for (const IdentifierReference &Reference : References) {
    Result.push_back(json::Object{{"offset", Reference.Offset},
                                  {"length", Reference.Length},
                                  {"kind", Reference.Kind},
                                  {"symbol", Reference.Symbol}});
  }
  return Result;
}

static json::Object dependenciesToJSON(const DependencyFacts &Dependencies) {
  return json::Object{
      {"functions", stringsToJSON(Dependencies.Functions)},
      {"globals", stringsToJSON(Dependencies.Globals)},
      {"enums", stringsToJSON(Dependencies.Enums)},
      {"declarations", stringsToJSON(Dependencies.Declarations)},
      {"address_taken_globals",
       stringsToJSON(Dependencies.AddressTakenGlobals)}};
}

static json::Object functionToJSON(const ExtractedFunction &Function) {
  json::Array Parameters;
  json::Array ForwardArguments;
  for (const auto &Parameter : Function.Parameters) {
    Parameters.push_back(
        json::Object{{"name", Parameter.first}, {"type", Parameter.second}});
    ForwardArguments.push_back(Parameter.first);
  }

  json::Array Attributes;
  for (const std::string &Attribute : Function.Attributes)
    Attributes.push_back(Attribute);
  json::Array PriorDeclarations;
  for (const SourceSliceFact &Declaration : Function.PriorDeclarations) {
    PriorDeclarations.push_back(
        json::Object{{"source_path", Declaration.SourcePath},
                     {"start_offset", Declaration.StartOffset},
                     {"end_offset", Declaration.EndOffset},
                     {"source", Declaration.Source}});
  }

  return json::Object{
      {"symbol", Function.Symbol},
      {"name_spelling", Function.NameSpelling},
      {"source_form", Function.SourceForm},
      {"source_path", Function.SourcePath},
      {"start_offset", Function.StartOffset},
      {"end_offset", Function.EndOffset},
      {"start_line", Function.StartLine},
      {"end_line", Function.EndLine},
      {"name_offset", Function.NameOffset},
      {"name_length", Function.NameLength},
      {"body_start_offset", Function.BodyStartOffset},
      {"body_end_offset", Function.BodyEndOffset},
      {"source", Function.Source},
      {"signature", Function.Signature},
      {"return_type", Function.ReturnType},
      {"return_category", Function.ReturnCategory},
      {"storage", Function.Storage},
      {"variadic", Function.Variadic},
      {"has_unnamed_parameter", Function.HasUnnamedParameter},
      {"parameters", std::move(Parameters)},
      {"forward_arguments", std::move(ForwardArguments)},
      {"attributes", std::move(Attributes)},
      {"prior_declarations", std::move(PriorDeclarations)},
      {"macros", stringsToJSON(Function.Macros)},
      {"dependencies", dependenciesToJSON(Function.Dependencies)},
      {"references", referencesToJSON(Function.References)}};
}

static json::Object globalToJSON(const ExtractedGlobal &Global) {
  json::Array Attributes;
  for (const std::string &Attribute : Global.Attributes)
    Attributes.push_back(Attribute);

  return json::Object{
      {"symbol", Global.Symbol},
      {"name_spelling", Global.NameSpelling},
      {"source_form", Global.SourceForm},
      {"source_path", Global.SourcePath},
      {"start_offset", Global.StartOffset},
      {"end_offset", Global.EndOffset},
      {"start_line", Global.StartLine},
      {"end_line", Global.EndLine},
      {"name_offset", Global.NameOffset},
      {"name_length", Global.NameLength},
      {"source", Global.Source},
      {"type", Global.Type},
      {"storage", Global.Storage},
      {"has_initializer", Global.HasInitializer},
      {"attributes", std::move(Attributes)},
      {"macros", stringsToJSON(Global.Macros)},
      {"dependencies", dependenciesToJSON(Global.Dependencies)},
      {"references", referencesToJSON(Global.References)}};
}

static json::Object includeToJSON(const IncludeUse &Include) {
  return json::Object{{"offset", Include.Offset},
                      {"source_path", Include.SourcePath},
                      {"written", Include.Written},
                      {"resolved", Include.Resolved},
                      {"angled", Include.Angled}};
}

static json::Object
declarationToJSON(const ExtractedDeclaration &Declaration) {
  return json::Object{
      {"kind", Declaration.Kind},
      {"name", Declaration.Name},
      {"identifiers", stringsToJSON(Declaration.Identifiers)},
      {"source_path", Declaration.SourcePath},
      {"start_offset", Declaration.StartOffset},
      {"end_offset", Declaration.EndOffset},
      {"start_line", Declaration.StartLine},
      {"end_line", Declaration.EndLine},
      {"source", Declaration.Source}};
}

static json::Object
macroDefinitionToJSON(const MacroDefinitionFact &Definition) {
  return json::Object{{"name", Definition.Name},
                      {"source_path", Definition.SourcePath},
                      {"start_offset", Definition.StartOffset},
                      {"end_offset", Definition.EndOffset},
                      {"source", Definition.Source}};
}

} // namespace

int main(int argc, const char **argv) {
  auto OptionsParser =
      CommonOptionsParser::create(argc, argv, ExtractorCategory);
  if (!OptionsParser) {
    errs() << toString(OptionsParser.takeError()) << '\n';
    return 2;
  }

  if (RequestedFunctions.empty() && RequestedGlobals.empty() &&
      !AllMainFunctions && !AllMainGlobals) {
    errs() << "at least one selector or --all-main-* option is required\n";
    return 2;
  }

  StringSet<> RequestedFunctionSet;
  for (const std::string &Name : RequestedFunctions)
    RequestedFunctionSet.insert(Name);
  StringSet<> RequestedGlobalSet;
  for (const std::string &Name : RequestedGlobals)
    RequestedGlobalSet.insert(Name);

  std::vector<ExtractedFunction> Functions;
  std::vector<ExtractedGlobal> Globals;
  std::vector<ExtractedDeclaration> Declarations;
  std::vector<IncludeUse> Includes;
  std::vector<MacroDefinitionFact> MacroDefinitions;
  bool HadDiagnosticError = false;
  ClangTool Tool(OptionsParser->getCompilations(),
                 OptionsParser->getSourcePathList());
  Tool.appendArgumentsAdjuster(stripGCCOnlyKernelArguments);
  ExtractorActionFactory Factory(RequestedFunctionSet, RequestedGlobalSet,
                                 Functions, Globals, Declarations, Includes,
                                 MacroDefinitions, HadDiagnosticError);
  int ToolResult = Tool.run(&Factory);
  if (ToolResult != 0)
    return ToolResult;
  if (HadDiagnosticError) {
    errs() << "Clang reported source diagnostics; extraction rejected\n";
    return 5;
  }

  std::sort(Functions.begin(), Functions.end(),
            [](const ExtractedFunction &Left,
               const ExtractedFunction &Right) {
              return std::tie(Left.SourcePath, Left.StartOffset) <
                     std::tie(Right.SourcePath, Right.StartOffset);
            });
  std::sort(Includes.begin(), Includes.end(),
            [](const IncludeUse &Left, const IncludeUse &Right) {
              return std::tie(Left.SourcePath, Left.Offset, Left.Written) <
                     std::tie(Right.SourcePath, Right.Offset, Right.Written);
            });
  std::sort(Globals.begin(), Globals.end(),
            [](const ExtractedGlobal &Left, const ExtractedGlobal &Right) {
              return std::tie(Left.SourcePath, Left.StartOffset) <
                     std::tie(Right.SourcePath, Right.StartOffset);
            });
  std::sort(Declarations.begin(), Declarations.end(),
            [](const ExtractedDeclaration &Left,
               const ExtractedDeclaration &Right) {
              return std::tie(Left.SourcePath, Left.StartOffset,
                              Left.EndOffset, Left.Kind) <
                     std::tie(Right.SourcePath, Right.StartOffset,
                              Right.EndOffset, Right.Kind);
            });
  std::sort(MacroDefinitions.begin(), MacroDefinitions.end(),
            [](const MacroDefinitionFact &Left,
               const MacroDefinitionFact &Right) {
              return std::tie(Left.SourcePath, Left.StartOffset, Left.Name) <
                     std::tie(Right.SourcePath, Right.StartOffset, Right.Name);
            });

  StringSet<> FoundFunctions;
  json::Array FunctionJSON;
  for (const ExtractedFunction &Function : Functions) {
    FoundFunctions.insert(Function.Symbol);
    FunctionJSON.push_back(functionToJSON(Function));
  }
  StringSet<> FoundGlobals;
  json::Array GlobalJSON;
  for (const ExtractedGlobal &Global : Globals) {
    FoundGlobals.insert(Global.Symbol);
    GlobalJSON.push_back(globalToJSON(Global));
  }

  bool Missing = false;
  for (const auto &Entry : RequestedFunctionSet) {
    if (!FoundFunctions.contains(Entry.getKey())) {
      errs() << "requested function definition not found: "
             << Entry.getKey() << '\n';
      Missing = true;
    }
  }
  for (const auto &Entry : RequestedGlobalSet) {
    if (!FoundGlobals.contains(Entry.getKey())) {
      errs() << "requested global definition not found: "
             << Entry.getKey() << '\n';
      Missing = true;
    }
  }
  if (Missing)
    return 3;

  json::Array IncludeJSON;
  for (const IncludeUse &Include : Includes)
    IncludeJSON.push_back(includeToJSON(Include));

  json::Array DeclarationJSON;
  for (const ExtractedDeclaration &Declaration : Declarations)
    DeclarationJSON.push_back(declarationToJSON(Declaration));
  json::Array MacroDefinitionJSON;
  for (const MacroDefinitionFact &Definition : MacroDefinitions)
    MacroDefinitionJSON.push_back(macroDefinitionToJSON(Definition));

  json::Object Root{{"schema_version", 1},
                    {"offset_encoding", "utf-8-bytes"},
                    {"functions", std::move(FunctionJSON)},
                    {"globals", std::move(GlobalJSON)},
                    {"includes", std::move(IncludeJSON)},
                    {"declarations", std::move(DeclarationJSON)},
                    {"macro_definitions", std::move(MacroDefinitionJSON)}};

  std::error_code Error;
  raw_fd_ostream Output(OutputPath, Error, sys::fs::OF_Text);
  if (Error) {
    errs() << "cannot open " << OutputPath << ": " << Error.message()
           << '\n';
    return 4;
  }
  Output << formatv("{0:2}\n", json::Value(std::move(Root)));
  return 0;
}
