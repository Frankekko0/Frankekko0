// Copies the shared Vinted parser configuration (backend/app/acquisition/vinted_parser.json)
// into the extension as src/parser-config.js, the copy used until FlipFinder sends a newer one.
//   node extension/tools/sync-parser-config.mjs          write the copy
//   node extension/tools/sync-parser-config.mjs --check  exit 1 if the copy is out of date
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = join(here, "..", "..", "backend", "app", "acquisition", "vinted_parser.json");
const target = join(here, "..", "src", "parser-config.js");

export function render(json) {
  return (
    "/* Generated from backend/app/acquisition/vinted_parser.json by tools/sync-parser-config.mjs.\n" +
    " * Do not edit: change the JSON (the server and the extension share it) and run the tool. */\n" +
    `globalThis.FF_PARSER_CONFIG = ${JSON.stringify(JSON.parse(json), null, 2)};\n`
  );
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const wanted = render(readFileSync(source, "utf8"));
  if (process.argv.includes("--check")) {
    const current = readFileSync(target, "utf8");
    if (current !== wanted) {
      console.error("src/parser-config.js is out of date: run node extension/tools/sync-parser-config.mjs");
      process.exit(1);
    }
    console.log("parser-config.js is up to date");
  } else {
    writeFileSync(target, wanted);
    console.log(`wrote ${target}`);
  }
}
