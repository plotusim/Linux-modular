# Architecture and safety model

## Pipeline contracts

The production pipeline is a sequence of versioned artifacts:

1. configured kernel ELF objects plus matching LLVM sidecar bitcode (or
   legacy ELF objects containing `.llvmbc`);
2. one JSONL reference-fact stream per translation unit;
3. a linkage-aware reference graph plus points-to solution;
4. boot observations, final-ELF phase accounting and an explicit planner
   policy;
5. an optional dependency-aware existing-Kconfig module profile;
6. an explainable residual function module plan;
7. Clang AST source-extraction facts, a source-local declaration/macro fixed
   point, plus an LLVM-checked private source closure and resident frontier;
8. a hash-checked source/Kbuild/Kconfig bundle;
9. build, resident-size, balanced startup A/B and QEMU functional results.

Every filesystem hand-off either has a schema version or a content hash.
Clang offsets are tagged as UTF-8 byte offsets, normalized to Python character
indices before editing, and rechecked against the current source before
generation. Bundle application verifies the original source hash again. This
deliberately turns stale or mis-encoded analysis into a hard failure.

## Reference graph

External functions use `fn:external:<symbol>`. Translation-unit-local
functions use `fn:internal:<translation-unit>:<symbol>`. Globals follow the
same rule. This prevents common Linux names such as `probe`, `show` and
`remove` from collapsing into one node.

The LLVM pass emits:

- direct calls;
- address-taken facts;
- global initializer references, including callback tables;
- copy, load, store, GEP/field, argument and return constraints;
- linker-section and export-table evidence;
- syscall-table membership recovered from `sys_call_table` initializers;
- function attributes including `inlinehint`, `__always_inline`, init/head,
  tracing and context-sensitive markers;
- explicit indirect call sites.

The Python solver propagates points-to sets to a fixed point. Each indirect
site becomes one or more `indirect_call` edges. A site with no target becomes
an `unresolved_call` edge to a dedicated unresolved node; it is never silently
dropped.

Opaque-pointer IR erases many source types, so the solver does not rely on an
LLVM struct name alone. It combines exact field paths, byte-offset layout
shapes, function parameters/returns, global ownership and object provenance.
When a coarse memory family saturates, a bounded backward slice can still
recover a precise call-site field or object. The unresolved record keeps that
provenance so later analysis can explain which alias family lost precision.

On whole-kernel inputs, exact points-to sets can still exceed workstation
memory. The bounded Roaring work-list solver marks an over-budget variable as
saturated, propagates that taint, removes any misleading partial target set
from affected calls, and emits unresolved edges. Candidate planning then marks
the caller and conservative potential target closure UNKNOWN. Saturation is
never interpreted as an empty target set.

Whole-kernel ratios use only the objects named by `KBUILD_VMLINUX_OBJS`.
The bitcode extractor can enumerate the thin `built-in.a`/`lib.a` link inputs
and records that selection in its manifest. Build-directory vDSOs, host tools,
and objects already linked into `.ko` files are excluded from the resident
kernel denominator.

Before planning, ELF enrichment reads each selected object's symbol table and
relocations:

- `nm -S` supplies function/global machine-code sizes;
- `__ksymtab_*` entries restore exports that IR declarations alone cannot
  identify reliably;
- `.pci_fixup_*` relocations identify direct registered callbacks;
- `.x86_cpu_dev.init` relocations identify immutable CPU-vendor callback
  tables. The planner follows their `GLOBAL_INITIALIZER` edges to seed the
  actual functions, even under a phase-aware policy that excludes ordinary
  data edges.

The matching is object-scoped, so two same-named translation-unit-local
functions cannot inherit each other's size, export or registration evidence.
Compiler-generated string literals, LLVM retention metadata and
`.discard.addressable` records are not treated as movable runtime state.

## Startup-first accounting and existing module profile

Startup necessity is reported independently of planner disposition. The
final linked `vmlinux` positive-size function multiset is matched to graph
nodes by exact `(symbol, size_bytes)` and partitioned into `BOOT_HOT`,
`POST_BOOT_ONLY`, `BOOT_COLD`, and `UNKNOWN`. A pre-loader observation always
wins over a post-loader or absent classification. Unmatched linked symbols
remain `UNKNOWN`. The same report aggregates source roots and finds source
units whose matched functions are entirely on the defer side. This is a
workload-specific upper bound, not permission to extract those functions.

Before creating a new function boundary, the optional Kconfig profile stage
uses the kernel's existing module ownership. It loads the supplied baseline
with Kconfiglib, explicitly requests only originally-`y` tristates that are
currently assignable to `m`, iterates dependency changes to a fixed point,
and rejects any change to an explicit deployment keep-list. Every effective
transition is reported, including dependency and choice side effects. A real
kernel build, module-unloaded boot A/B, and representative deferred-feature
load tests remain mandatory; the generated profile is never assumed to be a
universal configuration.

## Boundary planner

The planner first computes the hard resident closure from:

- policy boot roots and functions observed before the loader-ready boundary;
- init/head/noinstr and other hard attributes or sections;
- syscall entries, linker-registered callbacks and exported permanent APIs;
- IRQ, NMI, atomic, early-boot and unknown contexts;
- resident APIs whose process-context load safety is not proven.

Remaining functions are grouped into link-closed units. One-way calls,
callbacks, recursion and privately owned global state stay in the same unit.
A candidate is rejected when it shares mutable state with core code or
references a non-exported core function/global. Unknown definitions and
unresolved indirect calls remain `UNKNOWN`.

Static size estimates rank candidates and reject obviously unprofitable
boundaries (4096 bytes minimum by default), but never authorize deployment.
The default resident estimate is 640 bytes for the first interface and 96
bytes for each additional interface in the same candidate. The shared
noinline loader and single typed operations-table export measured 995 bytes
for seven signal interfaces (a 1,216-byte estimate) and below 832 bytes for
three stat interfaces.
When `aggregate_savings_groups` is enabled, multiple independently safe,
positive-net candidates may satisfy the threshold as one deployment group.
Aggregation can remove only the minimum-size reason; it never hides phase,
context, boundary, initialization or missing-evidence failures. The
post-build ELF and startup-resource gates remain authoritative.

The plan summary reports a viability funnel in addition to READY/BLOCKED:
positive static net bytes, presence of a resident interface, presence of a
real direct/indirect/assembly execution edge, proven process-context
load-safety, and the configured savings threshold. This prevents a callback
that is merely address-taken from being mistaken for a usable lazy-load
boundary.

Some architecture compatibility syscall tables are generated outside the
LLVM translation unit that defines their wrappers. A reviewed policy may
declare reserved ABI wrapper prefixes such as `__ia32_sys_`. Matching wrappers
remain resident and receive the same process-context boundary proof as
table-tagged syscall entries. This is explicit architecture policy, not a
general name heuristic, and it does not match internal `__do_sys_*` helpers.

Final accounting uses positive-size defined function/text symbol rows in the
linked `vmlinux`, not the number of IR nodes. Graph functions are reconciled
by exact `(symbol, size_bytes)` multisets. Linker/assembly/generated functions
without an exact graph match are UNKNOWN. A READY function is counted as
successfully modularized only after extraction, kernel build, QEMU boot,
modprobe, unload, permanent-size and startup A/B validation all pass.
Reports that relax an optional compressed-image ceiling must label that set as
mechanism-validated; a strict release set additionally requires its configured
image gate, so exploratory candidates cannot silently inflate release totals.

## Module-loader phase evidence

Boot-observation schema v2 preserves the first/last ftrace timestamp, observed
execution contexts and phase memberships for every mapped function. The
initramfs writes the loader-ready trace marker only after verifying the kernel
module interface, configured userspace modprobe path, `/sbin/modprobe`,
`modules.dep`, sysfs and tracefs. Planning with
`enforce_loader_ready_phase=true` requires a marker in every trace and rejects
any observed function that lacks a phase.

Phase sets can overlap: a function executed on both sides of the marker is
both pre- and post-loader. Reports therefore include disjoint pre-only,
post-only and both counts instead of treating the raw membership counts as a
partition.

Candidates receive one of four phase classifications:

- `EARLY_CORE`: the candidate or an execution boundary ran before loader
  readiness and must remain resident;
- `LAZY_READY`: every executable boundary has loader-phase and sleeping
  process-context proof;
- `PRELOAD`: the feature is late but an IRQ/NMI/atomic boundary cannot call
  `request_module()`; it needs an explicit load-before-registration design;
- `UNKNOWN_PHASE`: the phase or executable-boundary proof is incomplete.

Only `LAZY_READY` can become demand-load READY. `PRELOAD` remains
`NEEDS_EVIDENCE`; it is never silently treated as a sleeping boundary.
Observed absence is still workload-scoped negative evidence. See
[the loader-phase model](LOADER_PHASE_MODEL.md) for the runtime contract and
the Linux 5.10.176 measurement.

## Generated lazy boundary

For each resident interface the generator retains the original symbol and
signature as a wrapper. The moved implementation is renamed inside the
module. All interfaces of one module are published through one typed,
content-addressed operations table:

1. one non-inlined loader per resident source calls `symbol_get()` for the
   operations table;
2. a successful lookup pins the implementation module;
3. on the first miss, a proven process-context loader calls
   `request_module()` and retries `symbol_get()`;
4. each wrapper invokes its ABI-checked function-pointer field and then calls
   `symbol_put()` for the table;
5. module unload waits for every outstanding strong module reference.

Atomic-context calls fail using the generated or policy-supplied failure
expression; they never attempt to load. The boundary allocates no heap memory
and needs no per-interface RCU pointer, owner state, spinlock, publish API or
withdrawal grace period. Sharing both the export and slow loader amortizes
ksymtab/kallsyms strings and context checks across multi-interface candidates.
Module init and exit are therefore empty unless the moved feature itself
requires lifecycle work.

Compact exported-table and loader symbol names use a 48-bit Base32 content ID;
the related C type shares the same stem. This retains the previous 48-bit
collision strength while reducing permanent kallsyms and string-table bytes.
Non-emitted structure field names retain deterministic 64-bit IDs, while a
generated comment and manifest keep the original source identity. Linux
5.10.176 Kbuild, MODPOST and QEMU unload/reload tests validate that the table
reference pins module text for the complete call.

The inverse boundary—calls from moved code back into resident code—can also be
packed. With `pack_resident_dependencies`, every dependency whose AST
reference ranges are exact becomes a field in one exported, typed resident
table per bundle. Source-local and already-external resident dependencies
share that table. Resident globals use Clang's exact declared type rather than
module-side `typeof(symbol)`; arrays and function-pointer objects receive the
corresponding pointer declarator. The table is initialized in
`.data..ro_after_init`, so it becomes read-only after boot without perturbing
the final read-only segment's page alignment. A module static initializer that
requires a constant function address receives a same-signature local
trampoline, so the initializer remains a valid C constant expression. Macro
arguments mapped back to exact main-file tokens are rewritable; a reference
originating in a macro body or header inline is not. The reviewed
`direct_resident_dependencies` list forces only such a symbol back to a direct
export. All packed, trampoline and forced-direct decisions are recorded in the
manifest and still pass through MODPOST.

## Source rewriting

`SourceExtractor` uses Clang LibTooling and the configured kernel compilation
database. It records exact declaration/body/name/reference UTF-8 byte ranges,
signatures, parameter types and names, macro expansions, attributes,
dependencies and includes. It also records exact main-file definitions for
file-scope typedefs, complete enums, named records and preprocessor macros.
Function-local statics are deliberately not reported as translation-unit
globals: their declaration and initializer already belong to the owning
function slice. Ordinary prototypes that precede a selected definition are
recorded as `prior_declarations`, allowing the backend to remove a prototype
only when its non-interface implementation actually moves.
DeclRefExpr token ranges inside macro arguments are converted to file ranges;
locations produced by macro bodies or header inline code remain deliberately
unmapped.
Explicit selectors extract a reviewed set;
`--all-main-functions` and `--all-main-globals` discover a translation-unit
closure while excluding definitions whose spelling is generated by macros.
The candidate stage subsequently re-runs extraction with explicit same-main-
file dependencies that the first pass missed, until macro-generated entities
reach a fixed point. Header inline definitions are excluded from this retry.
When token pasting gives one macro invocation several AST global names, the
extractor records the invocation as a `macro_declaration_group` and uses the
longest matching source prefix/suffix identifier only for exact source
verification. Such a group remains resident until every co-definition can be
modeled and moved atomically.

With `--expand-private-source-closure`, the Python stage treats the planner
functions as seeds and follows exact AST dependencies through same-translation
unit functions and globals.  It then checks incoming LLVM ownership edges for
every reached entity.  A dependency still referenced by an unselected source
owner becomes a resident frontier instead of being moved.  Indirect and
unresolved target edges are not treated as link-time ownership by themselves;
the address-holding global initializer is checked separately.  Missing graph
coverage is a hard failure, and `source-closure.json` records every moved
entity, frontier user and edge kind.

A reviewed `--promote-callback-table` selector uses LLVM
`GLOBAL_INITIALIZER` edges to add same-source function fields as additional
lazy interfaces. The original table and same-named wrappers remain resident;
only their implementations join the private closure. Fields with teardown,
atomic-context or other unsuitable semantics can be removed with
`--exclude-interface`. Promotion is table-scoped and explicit because an
arbitrary callback table is not proof of sleepable execution or safe object
lifetime.

Ownership is not the only requirement for a reloadable boundary. A mutable
same-translation-unit global with an LLVM `GLOBAL_WRITE`, or a mutable global
whose address escapes, is intrinsic module-lifetime state and remains
resident even when no other owner is visible. Weak function definitions also
remain resident to preserve link-time override semantics. A function whose
address escapes remains resident because an already-published callback may
outlive the module. These checks prevent an empty module exit from resetting
kernel state or leaving a dangling function pointer.
LLVM may represent `vma->vm_ops = &table` as an ordinary global read and lose
the fact that the data address is published. The extractor therefore records
direct unary-address uses in `dependencies.address_taken_globals`. A reached
global with this fact is an intrinsic `source_address_taken_global` frontier,
including const tables, until a subsystem-specific lifetime pin proves that
the object cannot outlive the module.

After ownership closes, the backend computes a per-translation-unit fixed
point over identifiers used by moved entities and packed resident signatures.
It copies only reached declarations and lexically active macro definitions,
recursively following support dependencies. Overlapping Clang declarations
are collapsed to their outer owner, while conflicting local names from
different translation units are rejected. A generated public lazy ABI may not
name one of these private types because the narrow interface header cannot
make it visible on both sides without redefinition.

External resident exports may be inferred from AST dependencies only when ELF
enrichment proves that the configured kernel export surface was imported. The
inferred symbol must have one external-linkage source definition of the right
entity kind and must not already be exported, inline, builtin or supplied by
an earlier bundle. A raw LLVM graph therefore never treats a missing
`exported` attribute as proof that a source edit is required.

Multiple planner candidates may form one composite ownership domain.  The
primary must be READY.  A merged NEEDS_EVIDENCE companion is accepted only
when its sole gap is missing machine-code size, its phase is LAZY_READY and
all executable interface edges are load-safe; real extraction, Kbuild and the
size gate then replace the missing estimate.  Composite selection never
repairs phase, context or unresolved-call uncertainty.  Compat syscall macro
forms (`__se_compat_sys_*`/`__do_compat_sys_*`) are recovered as ordinary
module implementations by the AST extractor.

The Python backend:

- converts tagged UTF-8 byte offsets without quadratic rescanning, then
  verifies every recorded slice and identifier against current source;
- performs only non-overlapping source-range replacements;
- deduplicates identical AST edits and rejects overlapping or conflicting
  edits before touching source;
- renames declarations and references using AST-provided identifier ranges;
- moves only globals wholly owned by the candidate and not classified as
  module-lifetime state; extracted resident globals stay in the original
  source and provide exact dependency-table types;
- emits reached source-local declarations and macros in original lexical
  order before generated module declarations;
- may duplicate only explicitly reviewed static helpers, leaving their
  resident definitions untouched;
- may remove `static` only for explicit `resident_exports`, records every
  external resident export in the manifest, and still relies on MODPOST;
- may replace exact resident dependency references with fields of one typed
  read-only table, using ABI-matched trampolines for constant initializers and
  explicit direct-export exceptions for non-rewritable macro/inline uses;
- emits only audited single-line constant or function-like `module_defines`
  accepted by restrictive declarator and expression grammars;
- rejects source markers with special lifetime/section semantics;
- preserves original includes and emits a narrow generated interface header;
- canonicalizes source-local quoted includes relative to the generated module
  directory so unrelated subsystem headers with names such as `internal.h`
  cannot collide after multiple bundles add Kbuild include paths;
- produces content-addressed bundles that can be composed transactionally,
  replayed idempotently and rolled back in reverse transaction order.

No regex is used to discover C function boundaries.

## Validation philosophy

Passing requires all of the following:

- graph schema and endpoint validation;
- zero unaccepted `UNKNOWN` decisions;
- successful AST extraction with no compiler diagnostics;
- syntax checks and a real kernel build with MODPOST;
- at least the configured permanent resident ELF-byte savings (4096 bytes by
  default), a final-linked-`vmlinux` permanent-range regression ceiling, and an
  optional compressed-image growth ceiling;
- baseline behavior equivalence;
- QEMU proof of initial absence, autoload, unload and reload;
- balanced adjacent A/B startup samples with no configured kernel-memory,
  ready-time, available-memory or Slab regression.

Ftrace coverage is scenario evidence, not a proof that every unobserved
function is dead. Additional workloads can only add hard roots; absence alone
does not relax the static safety constraints.

Object-level section savings are attribution evidence, not automatically a
startup-memory claim. Linker script boundaries and page alignment can absorb
several kilobytes without releasing a page. The linked-kernel report therefore
measures the final code, read-only, writable and BSS ranges separately and
gates their exact permanent total; a runtime memory reduction is claimed only
when the final layout or a measured post-boot release actually crosses the
relevant allocation granularity.

`analyze-linker-alignment` additionally measures a configurable aligned text
island. On x86 its defaults use `__kprobes_text_end`,
`__entry_text_start/end`, and `__softirqentry_text_start` with the 2 MiB PMD
alignment. It reports how much pre-island payload moved, how much was absorbed
as padding, and the remaining reduction needed to reach the previous boundary.
This diagnostic is a target estimator only; the final linked-kernel gate stays
authoritative after the threshold is crossed.
