// Module-state facts for text splits. An ES module import is a read-only live
// binding, so a symbol must stay in the file of every statement that reassigns
// it; and an imported module runs before the importer's body, so a moved
// initializer runs earlier than the top-level statements that preceded it.

export function isPropertyName(ts, node) {
  const parent = node.parent
  if (!parent) {
    return false
  }
  if (ts.isPropertyAccessExpression(parent)) {
    return parent.name === node
  }
  if (ts.isQualifiedName(parent)) {
    return parent.right === node
  }
  if (ts.isBindingElement(parent)) {
    return parent.propertyName === node
  }
  if (ts.isLabeledStatement(parent) || ts.isBreakOrContinueStatement(parent)) {
    return parent.label === node
  }
  return (
    (ts.isPropertyAssignment(parent) ||
      ts.isPropertyDeclaration(parent) ||
      ts.isPropertySignature(parent) ||
      ts.isMethodDeclaration(parent) ||
      ts.isMethodSignature(parent) ||
      ts.isGetAccessorDeclaration(parent) ||
      ts.isSetAccessorDeclaration(parent) ||
      ts.isEnumMember(parent) ||
      ts.isJsxAttribute(parent)) &&
    parent.name === node
  )
}

function assignedNames(ts, target, names) {
  if (ts.isIdentifier(target)) {
    names.push(target.text)
  } else if (ts.isParenthesizedExpression(target) || ts.isSpreadElement(target)) {
    assignedNames(ts, target.expression, names)
  } else if (ts.isArrayLiteralExpression(target)) {
    for (const element of target.elements) {
      assignedNames(ts, element, names)
    }
  } else if (ts.isObjectLiteralExpression(target)) {
    for (const property of target.properties) {
      if (ts.isShorthandPropertyAssignment(property)) {
        names.push(property.name.text)
      } else if (ts.isPropertyAssignment(property)) {
        assignedNames(ts, property.initializer, names)
      } else if (ts.isSpreadAssignment(property)) {
        assignedNames(ts, property.expression, names)
      }
    }
  } else if (
    ts.isBinaryExpression(target) &&
    target.operatorToken.kind === ts.SyntaxKind.EqualsToken
  ) {
    assignedNames(ts, target.left, names)
  }
  return names
}

function writtenNames(ts, node) {
  if (ts.isBinaryExpression(node)) {
    const kind = node.operatorToken.kind
    if (kind >= ts.SyntaxKind.FirstAssignment && kind <= ts.SyntaxKind.LastAssignment) {
      return assignedNames(ts, node.left, [])
    }
  }
  if (
    (ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node)) &&
    (node.operator === ts.SyntaxKind.PlusPlusToken ||
      node.operator === ts.SyntaxKind.MinusMinusToken)
  ) {
    return assignedNames(ts, node.operand, [])
  }
  if (
    (ts.isForInStatement(node) || ts.isForOfStatement(node)) &&
    !ts.isVariableDeclarationList(node.initializer)
  ) {
    return assignedNames(ts, node.initializer, [])
  }
  return []
}

function runsEagerly(ts, node) {
  let eager = false
  function visit(child) {
    if (eager || ts.isFunctionLike(child) || ts.isClassLike(child)) {
      return
    }
    if (
      ts.isCallExpression(child) ||
      ts.isNewExpression(child) ||
      ts.isTaggedTemplateExpression(child) ||
      ts.isAwaitExpression(child) ||
      writtenNames(ts, child).length
    ) {
      eager = true
      return
    }
    ts.forEachChild(child, visit)
  }
  visit(node)
  return eager
}

function executesAtTopLevel(ts, statement) {
  return !(
    ts.isImportDeclaration(statement) ||
    ts.isExportDeclaration(statement) ||
    ts.isEmptyStatement(statement) ||
    ts.isVariableStatement(statement) ||
    ts.isFunctionDeclaration(statement) ||
    ts.isClassDeclaration(statement) ||
    ts.isInterfaceDeclaration(statement) ||
    ts.isTypeAliasDeclaration(statement) ||
    ts.isEnumDeclaration(statement) ||
    ts.isModuleDeclaration(statement) ||
    (ts.isExpressionStatement(statement) && ts.isStringLiteral(statement.expression))
  )
}

function pin(entry, reason) {
  // The first reason wins; later facts never overwrite it.
  entry.meta = { move_pinned_reason: reason, ...entry.meta }
}

// ponytail: names are matched lexically, so a local shadowing a module binding
// counts as a write to it. That only over-pins; add scope tracking if it bites.
export function annotateModuleState(ts, sourceFile, entries) {
  const names = new Set(entries.map((entry) => entry.name))
  const entryByNode = new Map(entries.map((entry) => [entry.bodyNode || entry.node, entry]))
  const writers = new Map()
  let firstEffectLine = null
  for (const statement of sourceFile.statements) {
    const roots = ts.isVariableStatement(statement)
      ? statement.declarationList.declarations
      : [statement]
    for (const root of roots) {
      const entry = entryByNode.get(root) || null
      if (
        entry &&
        firstEffectLine !== null &&
        ts.isVariableDeclaration(root) &&
        root.initializer &&
        runsEagerly(ts, root.initializer)
      ) {
        entry.meta = { ...entry.meta, eager_after_line: firstEffectLine }
        pin(entry, `initializer runs after the top-level statement at line ${firstEffectLine}`)
      }
      const owner = entry?.name ?? null
      const visit = (node) => {
        for (const name of writtenNames(ts, node)) {
          if (names.has(name) && name !== owner) {
            if (!writers.has(name)) {
              writers.set(name, new Set())
            }
            writers.get(name).add(owner)
          }
        }
        ts.forEachChild(node, visit)
      }
      visit(root)
    }
    if (firstEffectLine === null && executesAtTopLevel(ts, statement)) {
      firstEffectLine = sourceFile.getLineAndCharacterOfPosition(statement.getStart(sourceFile)).line + 1
    }
  }
  for (const entry of entries) {
    const by = writers.get(entry.name)
    if (!by) {
      continue
    }
    const named = [...by].filter(Boolean).sort()
    if (named.length) {
      entry.meta = { ...entry.meta, assigned_by: named }
    }
    if (by.has(null)) {
      entry.meta = { ...entry.meta, reassigned_at_top_level: true }
      pin(entry, "reassigned by top-level statements")
    }
  }
}
