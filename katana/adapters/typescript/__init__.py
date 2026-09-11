from __future__ import annotations

from pathlib import Path

from katana.adapters._builtin import BundledLanguageAdapter
from katana.external import source_ast_bridge_capabilities, typescript_ast_available
from katana.ir import AdapterCapabilities, MoveConstraints
from katana.models import SourceSplitMove, SourceSplitPlan, SourceSymbol


def _test_registrations(symbols: list[SourceSymbol]) -> list[SourceSymbol]:
    return [symbol for symbol in symbols if "test_registration" in symbol.tags]


def _validate_test_registration_moves(
    source: Path,
    moves: tuple[SourceSplitMove, ...],
    symbols: list[SourceSymbol],
) -> None:
    registrations = _test_registrations(symbols)
    if not registrations:
        return
    moved_to = {name: move.target.resolve() for move in moves for name in move.symbols}
    moved_registrations = [symbol for symbol in registrations if symbol.name in moved_to]
    if not moved_registrations:
        return
    if any(not (symbol.meta or {}).get("registration_rewrite_safe") for symbol in registrations):
        raise ValueError(
            f"Cannot move test registrations from {source}: "
            "unmodeled top-level executable statements could change execution order"
        )
    if len(moved_registrations) != len(registrations):
        raise ValueError(
            f"Cannot move only part of {source}: all top-level node:test registrations "
            "must move in one plan"
        )

    moved_names = set(moved_to)
    for registration in registrations:
        staying = [ref for ref in registration.references if ref not in moved_names]
        if staying:
            raise ValueError(
                f"Cannot move {registration.name}: source-local dependencies must move "
                f"with test registrations: {', '.join(staying)}"
            )

    target_runs: list[Path] = []
    for registration in registrations:
        target = moved_to[registration.name]
        if not target_runs or target_runs[-1] != target:
            target_runs.append(target)
    if len(target_runs) != len(set(target_runs)):
        raise ValueError(
            "Test registrations assigned to different targets must form contiguous "
            "source-order groups"
        )
    source_order = {symbol.name: index for index, symbol in enumerate(registrations)}
    for move in moves:
        names = [name for name in move.symbols if name in source_order]
        if names != sorted(names, key=source_order.__getitem__):
            raise ValueError("Test registrations must preserve source order within each target")


class TypeScriptAdapter(BundledLanguageAdapter):
    id = "node_typescript"
    extensions = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    profiles = ("web", "admin", "next", "all")
    reference_aware = True
    legacy_adapter_payload = True

    def language_for(self, path: Path) -> str:
        return {
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".jsx": "javascriptreact",
            ".mjs": "javascript",
            ".cjs": "javascript",
        }[path.suffix]

    def capabilities(self, path: Path) -> AdapterCapabilities:
        bridge = source_ast_bridge_capabilities(path)
        available = typescript_ast_available(path)
        return AdapterCapabilities(
            adapter_id=self.id,
            language=self.language_for(path),
            parser="typescript_compiler_ast" if available else "typescript_structural",
            strategy=(
                "typescript_compiler_api_source_file"
                if available
                else "top_level_declarations_and_braces"
            ),
            precision="semantic_top_level" if available else "structural_top_level",
            safe_apply=False,
            hierarchy=available,
            comments=available,
            local_bindings=available,
            module_rewrite=True,
            formatting=True,
            validation=True,
            adapter_available=available,
            verifications=("typescript_check", "frontend_tests"),
            limitations=(
                ("does not rewrite project-wide imports",)
                if available
                else (
                    "TypeScript compiler API is not available",
                    "does not rewrite project-wide imports",
                )
            ),
            bridge=bridge,
        )

    def outline(self, path: Path) -> list[SourceSymbol]:
        from katana.outline import outline_typescript_module

        return outline_typescript_module(path)

    def constraints(self, path: Path, symbols: list[SourceSymbol]) -> dict[str, MoveConstraints]:
        registrations = _test_registrations(symbols)
        registration_names = tuple(symbol.name for symbol in registrations)
        constraints: dict[str, MoveConstraints] = {}
        for symbol in symbols:
            meta = symbol.meta or {}
            if "test_registration" in symbol.tags:
                safe = bool(meta.get("registration_rewrite_safe"))
                constraints[symbol.name] = MoveConstraints(
                    movability="required_group" if safe else "pinned",
                    order_policy="preserve",
                    required_with=(
                        tuple(name for name in registration_names if name != symbol.name)
                        if safe
                        else ()
                    ),
                    pinned_reason=(None if safe else "unmodeled top-level executable statements"),
                )
                continue
            constraints[symbol.name] = MoveConstraints(
                movability="pinned" if meta.get("default_export") else "closure",
                pinned_reason="default export" if meta.get("default_export") else None,
            )
        return constraints

    def plan_split(self, source: Path, moves: tuple[SourceSplitMove, ...]) -> SourceSplitPlan:
        from katana.apply import plan_text_split

        _validate_test_registration_moves(source, moves, self.outline(source))
        return plan_text_split(source, moves)


__all__ = ("TypeScriptAdapter",)
