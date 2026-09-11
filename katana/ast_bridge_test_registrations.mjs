function nodeTestRegistrationBindings(ts, sourceFile) {
  const bindings = new Map()
  for (const statement of sourceFile.statements) {
    if (
      !ts.isImportDeclaration(statement) ||
      !ts.isStringLiteral(statement.moduleSpecifier) ||
      statement.moduleSpecifier.text !== "node:test"
    ) {
      continue
    }
    const namedBindings = statement.importClause?.namedBindings
    if (!namedBindings || !ts.isNamedImports(namedBindings)) {
      continue
    }
    for (const element of namedBindings.elements) {
      const imported = element.propertyName?.text || element.name.text
      if (imported === "describe") {
        bindings.set(element.name.text, imported)
      }
    }
  }
  return bindings
}

function testRegistrationEntry(ts, sourceFile, statement, bindings) {
  if (!ts.isExpressionStatement(statement) || !ts.isCallExpression(statement.expression)) {
    return null
  }
  const call = statement.expression
  if (!ts.isIdentifier(call.expression) || !bindings.has(call.expression.text)) {
    return null
  }
  const [title, callback] = call.arguments
  if (
    !title ||
    !(ts.isStringLiteral(title) || ts.isNoSubstitutionTemplateLiteral(title)) ||
    !callback ||
    !(
      ts.isArrowFunction(callback) ||
      ts.isFunctionExpression(callback) ||
      ts.isIdentifier(callback)
    )
  ) {
    return null
  }
  return {
    node: statement,
    bodyNode: statement,
    name: `test_registration@${statement.getStart(sourceFile)}`,
    kind: "TestRegistration",
    signature: `${bindings.get(call.expression.text)}(${JSON.stringify(title.text)}, ...)`,
    tags: ["test_registration"],
    meta: {
      test_registration: true,
      registration_callee: bindings.get(call.expression.text),
      registration_module: "node:test",
      title: title.text,
    },
  }
}

function unmodeledTopLevelKinds(ts, sourceFile, registrations) {
  const registrationStatements = new Set(registrations.map((entry) => entry.node))
  return sourceFile.statements
    .filter(
      (statement) =>
        !registrationStatements.has(statement) &&
        !ts.isImportDeclaration(statement) &&
        !ts.isExportDeclaration(statement) &&
        !ts.isEmptyStatement(statement) &&
        !ts.isFunctionDeclaration(statement) &&
        !ts.isInterfaceDeclaration(statement) &&
        !ts.isTypeAliasDeclaration(statement) &&
        !(
          ts.isExpressionStatement(statement) && ts.isStringLiteral(statement.expression)
        ),
    )
    .map((statement) => ts.SyntaxKind[statement.kind])
}

export function nodeTestRegistrationEntries(ts, sourceFile) {
  const bindings = nodeTestRegistrationBindings(ts, sourceFile)
  const entries = sourceFile.statements
    .map((statement) => testRegistrationEntry(ts, sourceFile, statement, bindings))
    .filter(Boolean)
  const unmodeledKinds = [...new Set(unmodeledTopLevelKinds(ts, sourceFile, entries))]
  for (const entry of entries) {
    entry.meta.registration_rewrite_safe = unmodeledKinds.length === 0
    entry.meta.unmodeled_top_level_kinds = unmodeledKinds
  }
  return entries
}
