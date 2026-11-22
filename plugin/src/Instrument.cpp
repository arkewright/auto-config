#include <charconv>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include <clang/AST/ASTConsumer.h>
#include <clang/AST/ASTContext.h>
#include <clang/AST/Attr.h>
#include <clang/AST/Decl.h>
#include <clang/AST/Expr.h>
#include <clang/Basic/Diagnostic.h>
#include <clang/Basic/SourceManager.h>
#include <clang/Frontend/CompilerInstance.h>
#include <clang/Frontend/FrontendPluginRegistry.h>
#include <llvm/ADT/SmallVector.h>
#include <llvm/Support/raw_ostream.h>

namespace {
using namespace clang;

struct Options {
  unsigned id{};
  std::string receipt;
};

void error(DiagnosticsEngine &diag, llvm::StringRef const text) {
  auto const id = diag.getCustomDiagID(DiagnosticsEngine::Error, "kcfgtrace: %0");
  diag.Report(id) << text;
}

bool excluded(FunctionDecl const &fd, SourceManager const &sources) {
  auto const loc = fd.getLocation();
  if (not sources.isWrittenInMainFile(sources.getSpellingLoc(loc)) or
      not sources.isWrittenInMainFile(sources.getExpansionLoc(loc)) or
      fd.isInvalidDecl() or fd.isImplicit() or fd.isInlined() or fd.getBuiltinID() != 0 or
      fd.getName().starts_with("kcfg_") or fd.getName() == "init_module" or
      fd.getName() == "cleanup_module")
    return true;

  auto const *type = fd.getType()->getAs<FunctionType>();
  if (not type or type->getCallConv() != CC_C or
      not FunctionEffectsRef::get(fd.getType()).empty())
    return true;
  for (auto const *attr : fd.attrs()) {
    if (llvm::isa<AlwaysInlineAttr, SectionAttr, PragmaClangTextSectionAttr,
                  NakedAttr, NoInstrumentFunctionAttr, NoProfileFunctionAttr,
                  NoSanitizeAttr, DisableSanitizerInstrumentationAttr,
                  NoStackProtectorAttr, PatchableFunctionEntryAttr, HotAttr, ColdAttr,
                  TargetAttr, TargetClonesAttr, TargetVersionAttr, PureAttr, ConstAttr,
                  LeafAttr, ReturnsTwiceAttr, AnyX86NoCallerSavedRegistersAttr,
                  AnyX86InterruptAttr, ARMInterruptAttr, AVRInterruptAttr,
                  M68kInterruptAttr, MSP430InterruptAttr, MipsInterruptAttr,
                  RISCVInterruptAttr>(attr))
      return true;
  }
  return false;
}

bool isProbeDeclaration(FunctionDecl const &fd, ASTContext const &ctx) {
  auto const *proto = fd.getType()->getAs<FunctionProtoType>();
  return proto and not proto->isVariadic() and proto->getNumParams() == 1 and
         ctx.hasSameType(proto->getParamType(0), ctx.UnsignedIntTy) and
         proto->getReturnType()->isVoidType() and proto->getCallConv() == CC_C and
         fd.getStorageClass() == SC_Extern and not fd.doesThisDeclarationHaveABody();
}

Stmt *makeCall(ASTContext &ctx, FunctionDecl &probe, unsigned const id,
               SourceLocation const loc) {
  auto const fn = probe.getType();
  auto *ref = DeclRefExpr::Create(ctx, {}, {}, &probe, false, loc, fn, VK_LValue);
  auto *callee = ImplicitCastExpr::Create(ctx, ctx.getPointerType(fn),
      CK_FunctionToPointerDecay, ref, nullptr, VK_PRValue, {});
  auto const value = llvm::APInt{ctx.getIntWidth(ctx.UnsignedIntTy), id};
  auto *arg = IntegerLiteral::Create(ctx, value, ctx.UnsignedIntTy, loc);
  return CallExpr::Create(ctx, callee, {arg}, ctx.VoidTy, VK_PRValue, loc, {});
}

struct InstrumentConsumer final : ASTConsumer {
  explicit InstrumentConsumer(CompilerInstance &compiler_, Options options_)
      : compiler{compiler_}, options{std::move(options_)} {}

  bool HandleTopLevelDecl(DeclGroupRef const group) override {
    for (auto *decl : group) {
      if (auto *fd = llvm::dyn_cast<FunctionDecl>(decl))
        process(*fd);
    }
    return true;
  }

  void HandleTranslationUnit(ASTContext &) override {
    if (compiler.getDiagnostics().hasErrorOccurred())
      return;
    if (not probe) {
      error(compiler.getDiagnostics(), "missing forced probe declaration");
      return;
    }
    // Let Clang discard this side output if code generation subsequently fails.
    if (auto out = compiler.createOutputFile(options.receipt, false, true, true))
      *out << count << '\n';
  }

private:
  void process(FunctionDecl &fd) {
    auto &ctx = fd.getASTContext();
    if (fd.getName() == "kcfg_hit") {
      if (not isProbeDeclaration(fd, ctx))
        error(compiler.getDiagnostics(), "expected extern void kcfg_hit(unsigned int)");
      else
        probe = &fd;
      return;
    }
    if (not fd.doesThisDeclarationHaveABody() or excluded(fd, ctx.getSourceManager()))
      return;
    auto *body = llvm::dyn_cast_or_null<CompoundStmt>(fd.getBody());
    if (not body)
      return;
    if (not probe) {
      error(compiler.getDiagnostics(), "force-include probe declaration before definitions");
      return;
    }
    auto const begin = body->getLBracLoc();
    auto statements = llvm::SmallVector<Stmt *, 16>{};
    statements.push_back(makeCall(ctx, *probe, options.id, begin));
    statements.append(body->body_begin(), body->body_end());
    fd.setBody(CompoundStmt::Create(ctx, statements, body->getStoredFPFeaturesOrDefault(),
                                   begin, body->getRBracLoc()));
    ++count;
  }

  CompilerInstance &compiler;
  Options options;
  FunctionDecl *probe{}; // Owned by this compiler's ASTContext.
  std::uint64_t count{};
};

struct InstrumentAction final : PluginASTAction {
  ActionType getActionType() override { return AddBeforeMainAction; }

  bool ParseArgs(CompilerInstance const &compiler, std::vector<std::string> const &args) override {
    auto const &inputs = compiler.getFrontendOpts().Inputs;
    if (inputs.size() != 1 or inputs.front().getKind().getLanguage() != Language::C or
        inputs.front().getKind().isPreprocessed()) {
      error(compiler.getDiagnostics(), "expected one unpreprocessed C source file");
      return false;
    }
    for (std::string_view arg : args) {
      if (arg.starts_with("id=") and options.id == 0) {
        arg.remove_prefix(3);
        auto const [end, ec] = std::from_chars(arg.data(), arg.data() + arg.size(), options.id);
        if (ec == std::errc{} and end == arg.data() + arg.size() and
            options.id > 0 and options.id <= 65535)
          continue;
      } else if (arg.starts_with("receipt=") and options.receipt.empty()) {
        arg.remove_prefix(8);
        if (not arg.empty() and arg != "-") {
          options.receipt = arg;
          continue;
        }
      }
      error(compiler.getDiagnostics(), "expected exactly id=1..65535 and receipt=PATH");
      return false;
    }
    if (options.id == 0 or options.receipt.empty()) {
      error(compiler.getDiagnostics(), "both id=1..65535 and receipt=PATH are required");
      return false;
    }
    return true;
  }

  std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &compiler,
                                                 llvm::StringRef) override {
    return std::make_unique<InstrumentConsumer>(compiler, std::move(options));
  }

private:
  Options options;
};

FrontendPluginRegistry::Add<InstrumentAction> const registration{
    "kcfgtrace", "insert workload evidence probes into selected C definitions"};
} // namespace
