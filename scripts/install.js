#!/usr/bin/env node

const fs = require("fs");
const os = require("os");
const path = require("path");

function usage() {
  console.log(`
cdr Codex Skill installer

Usage:
  npx --yes @supaboiclean/cdr@0.2.3
  cdr --skills-dir ~/.codex/skills

Options:
  --skills-dir PATH  Install into a custom Codex skills directory
  --help             Show this help
`);
}

function expandHome(value) {
  if (!value) return value;
  if (value === "~") return os.homedir();
  if (value.startsWith("~/")) return path.join(os.homedir(), value.slice(2));
  return value;
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") {
      options.help = true;
      continue;
    }
    if (arg === "--skills-dir") {
      const value = argv[index + 1];
      if (!value) throw new Error("--skills-dir requires a value");
      options.skillsDir = expandHome(value);
      index += 1;
      continue;
    }
    throw new Error(`Unknown option: ${arg}`);
  }
  return options;
}

function defaultSkillsDir() {
  const codexHome = process.env.CODEX_HOME || path.join(os.homedir(), ".codex");
  return path.join(codexHome, "skills");
}

function copyDirectory(source, destination) {
  fs.mkdirSync(destination, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    if (entry.name === "__pycache__" || entry.name.endsWith(".pyc")) continue;
    const sourcePath = path.join(source, entry.name);
    const destinationPath = path.join(destination, entry.name);
    if (entry.isDirectory()) copyDirectory(sourcePath, destinationPath);
    else if (entry.isFile()) fs.copyFileSync(sourcePath, destinationPath);
  }
}

function installSkill(source, destination) {
  if (!fs.existsSync(source)) throw new Error(`Cannot find bundled skill at ${source}`);
  fs.rmSync(destination, { recursive: true, force: true });
  copyDirectory(source, destination);
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    usage();
    return;
  }

  const packageRoot = path.resolve(__dirname, "..");
  const canonicalSource = path.join(packageRoot, "cdr");
  const legacySource = path.join(packageRoot, "code-rot-cleaner");
  const skillsDir = path.resolve(options.skillsDir || defaultSkillsDir());
  const canonicalDestination = path.join(skillsDir, "cdr");
  const legacyDestination = path.join(skillsDir, "code-rot-cleaner");

  fs.mkdirSync(skillsDir, { recursive: true });
  installSkill(canonicalSource, canonicalDestination);
  installSkill(legacySource, legacyDestination);

  console.log("Installed Codex skills:");
  console.log(`  Primary $cdr: ${canonicalDestination}`);
  console.log(`  Legacy alias $code-rot-cleaner: ${legacyDestination}`);
  console.log("");
  console.log("Use $cdr to audit possible code rot in report-only mode.");
  console.log("Codex usually detects skill changes automatically. If $cdr does not appear, restart Codex.");
}

try {
  main();
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exit(1);
}
