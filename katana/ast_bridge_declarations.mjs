import { nodeTestRegistrationEntries } from "./ast_bridge_test_registrations.mjs"

export function exported(ts, node) {
  return Boolean(ts.canHaveModifiers(node) && ts.getModifiers(node)?.some(
    (modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword,
  ))
}

export function defaultExported(ts, node) {
  return Boolean(ts.canHaveModifiers(node) && ts.getModifiers(node)?.some(
    (modifier) => modifier.kind === ts.SyntaxKind.DefaultKeyword,
  ))
}

export function declarationEntries(ts, sourceFile) {
  const entries = []
  const registrations = new Map(
    nodeTestRegistrationEntries(ts, sourceFile).map((entry) => [entry.node, entry]),
  )
  for (const statement of sourceFile.statements) {
    if (ts.isFunctionDeclaration(statement) || ts.isClassDeclaration(statement) ||
        ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement) ||
        ts.isEnumDeclaration(statement)) {
      if (statement.name || defaultExported(ts, statement)) {
        entries.push({ node: statement, name: statement.name?.text || "default_export",
          kind: ts.SyntaxKind[statement.kind] })
      }
    } else if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        for (const name of bindingNames(ts, declaration.name)) {
          entries.push({ node: statement, bodyNode: declaration, name,
            kind: ts.SyntaxKind[statement.declarationList.kind] })
        }
      }
    } else if (registrations.has(statement)) {
      entries.push(registrations.get(statement))
    } else if (ts.isExpressionStatement(statement) && ts.isCallExpression(statement.expression)) {
      const call = statement.expression
      if (ts.isPropertyAccessExpression(call.expression) &&
          /^(get|post|put|patch|delete|head|options|all|use)$/.test(call.expression.name.text) &&
          ts.isIdentifier(call.expression.expression) && call.expression.expression.text === "router") {
        entries.push({ node: statement, name: `route_${call.expression.name.text}_${statement.getStart(sourceFile)}`,
          kind: "RouteRegistration", tags: ["route_registration"],
          meta: { move_pinned_reason: "route registrations preserve execution order" } })
      }
    }
  }
  return entries
}

function bindingNames(ts, node) {
  if (ts.isIdentifier(node)) return [node.text]
  return node.elements.flatMap((element) => ts.isBindingElement(element)
    ? bindingNames(ts, element.name) : [])
}

export function byteSpan(sourceFile, node) {
  return {
    start_byte: Buffer.byteLength(sourceFile.text.slice(0, node.getStart(sourceFile)), "utf8"),
    end_byte: Buffer.byteLength(sourceFile.text.slice(0, node.getEnd()), "utf8"),
  }
}

export function requireCalls(ts, sourceFile, node) {
  const calls = []
  function visit(child) {
    if (ts.isCallExpression(child) && ts.isIdentifier(child.expression) &&
        child.expression.text === "require") {
      const argument = child.arguments[0]
      calls.push(argument && ts.isStringLiteral(argument)
        ? { ...byteSpan(sourceFile, argument), module: argument.text }
        : { dynamic: true })
    }
    ts.forEachChild(child, visit)
  }
  visit(node)
  return calls
}
