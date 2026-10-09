from __future__ import annotations

from dataclasses import replace
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
        from .rewrite import is_commonjs

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
            module_rewrite=available or not (path.exists() and is_commonjs(path)),
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

        from .rewrite import is_commonjs

        symbols = outline_typescript_module(path)
        if is_commonjs(path):
            return [
                replace(
                    symbol,
                    meta={
                        **(symbol.meta or {}),
                        "move_pinned_reason": "CommonJS rewrite requires the TypeScript compiler AST",
                    },
                )
                if "start_byte" not in (symbol.meta or {})
                else symbol
                for symbol in symbols
            ]
        return symbols

    def constraints(self, path: Path, symbols: list[SourceSymbol]) -> dict[str, MoveConstraints]:
        registrations = _test_registrations(symbols)
        registration_names = tuple(symbol.name for symbol in registrations)
        constraints: dict[str, MoveConstraints] = {}
        for symbol in symbols:
            meta = symbol.meta or {}
            if "route_registration" in symbol.tags:
                constraints[symbol.name] = MoveConstraints(
                    movability="pinned",
                    order_policy="preserve",
                    pinned_reason="route registrations preserve execution order",
                )
                continue
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
            reason = (
                "default export" if meta.get("default_export") else meta.get("move_pinned_reason")
            )
            constraints[symbol.name] = MoveConstraints(
                movability="pinned" if reason else "closure",
                required_with=tuple(meta.get("assigned_by", ())),
                pinned_reason=reason,
            )
        return constraints

    def plan_split(self, source: Path, moves: tuple[SourceSplitMove, ...]) -> SourceSplitPlan:
        from katana.apply import plan_text_split

        symbols = self.outline(source)
        from .rewrite import validate_dependencies

        _validate_test_registration_moves(source, moves, symbols)
        _validate_module_state_moves(source, moves, symbols)
        validate_dependencies(source, moves, symbols)
        plan = plan_text_split(source, moves)
        return SourceSplitPlan(
            plan.source,
            plan.moves,
            plan.moved_symbols,
            (*plan.warnings, *_eager_initializer_warnings(moves, symbols)),
        )


def _validate_module_state_moves(
    source: Path,
    moves: tuple[SourceSplitMove, ...],
    symbols: list[SourceSymbol],
) -> None:
    # An ES module import is a read-only binding: whoever reassigns a symbol
    # must end up in the same file as its declaration.
    location = {name: move.target.resolve() for move in moves for name in move.symbols}
    for symbol in symbols:
        meta = symbol.meta or {}
        if symbol.name in location and meta.get("reassigned_at_top_level"):
            raise ValueError(
                f"Cannot move {symbol.name} from {source}: it is reassigned by top-level "
                "statements that stay in the source, and an imported binding is read-only"
            )
        for writer in meta.get("assigned_by", ()):
            if location.get(writer) != location.get(symbol.name):
                raise ValueError(
                    f"Cannot split {symbol.name} and {writer} into different files: "
                    f"{writer} reassigns {symbol.name}, and an imported binding is read-only"
                )


def _eager_initializer_warnings(
    moves: tuple[SourceSplitMove, ...],
    symbols: list[SourceSymbol],
) -> list[str]:
    moved = {name for move in moves for name in move.symbols}
    return [
        f"{symbol.name} initializer will run before the top-level statement at line "
        f"{(symbol.meta or {})['eager_after_line']}; verify initialization order"
        for symbol in symbols
        if symbol.name in moved and (symbol.meta or {}).get("eager_after_line")
    ]


__all__ = ("TypeScriptAdapter",)
