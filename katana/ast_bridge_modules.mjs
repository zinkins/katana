import fs from "node:fs"
import { createRequire } from "node:module"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const requireFromHere = createRequire(import.meta.url)
const bridgeDir = path.dirname(fileURLToPath(import.meta.url))

function resolutionStarts(sourceFile) {
  return [
    process.cwd(),
    path.dirname(path.resolve(sourceFile || ".")),
    bridgeDir,
  ]
}

export function resolveOptional(name, sourceFile) {
  try {
    return requireFromHere.resolve(name, { paths: resolutionStarts(sourceFile) })
  } catch {
    return null
  }
}

export function loadOptional(name, sourceFile) {
  if (name === "typescript") {
    const searched = new Set()
    for (const start of resolutionStarts(sourceFile)) {
      try {
        const resolved = requireFromHere.resolve(name, { paths: [start] })
        if (searched.has(resolved)) continue
        searched.add(resolved)
        const ts = requireFromHere(resolved)
        if (hasCompilerApi(ts)) return ts
      } catch {
        // An unusable project package must not hide Katana's optional compiler.
      }
    }
    return null
  }
  const resolved = resolveOptional(name, sourceFile)
  if (!resolved) {
    return null
  }
  return requireFromHere(resolved)
}

function hasCompilerApi(ts) {
  return typeof ts?.createSourceFile === "function" &&
    typeof ts.forEachChild === "function" &&
    typeof ts.isIdentifier === "function" &&
    typeof ts.canHaveModifiers === "function" &&
    typeof ts.getModifiers === "function" &&
    typeof ts.ScriptTarget?.Latest === "number" &&
    typeof ts.ScriptKind?.TS === "number" &&
    typeof ts.SyntaxKind?.SourceFile === "number"
}

export async function importOptional(name, sourceFile) {
  const resolved = resolveImportOptional(name, sourceFile)
  return resolved ? import(pathToFileURL(resolved).href) : null
}

export function resolveImportOptional(name, sourceFile) {
  const parts = name.split("/")
  const scoped = name.startsWith("@")
  const packageParts = parts.slice(0, scoped ? 2 : 1)
  const subpathParts = parts.slice(scoped ? 2 : 1)
  const exportKey = subpathParts.length ? `./${subpathParts.join("/")}` : "."
  const starts = resolutionStarts(sourceFile)
  const searched = new Set()
  for (const start of starts) {
    for (let current = path.resolve(start); ; current = path.dirname(current)) {
      if (!searched.has(current)) {
        searched.add(current)
        const packageDir = path.join(current, "node_modules", ...packageParts)
        const manifestPath = path.join(packageDir, "package.json")
        if (fs.existsSync(manifestPath)) {
          try {
            const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"))
            const declared = manifest.exports?.[exportKey]
            const target =
              typeof declared === "string" ? declared : declared?.import || declared?.default
            if (target) {
              return path.resolve(packageDir, target)
            }
          } catch {
            // A malformed optional package is reported as unavailable.
          }
        }
      }
      const parent = path.dirname(current)
      if (parent === current) {
        break
      }
    }
  }
  return null
}
