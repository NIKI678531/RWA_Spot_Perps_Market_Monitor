/**
 * Lists translation keys used in src/ that a dictionary is missing.
 *
 * A missing key is not a crash — `t()` falls back to the Chinese source string — so
 * nothing else would ever tell us that switching to EN silently renders Chinese.
 *
 *   node scripts/i18n-audit.js
 */
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'src');

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return /\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

const used = new Set();
for (const file of walk(SRC)) {
  const text = fs.readFileSync(file, 'utf8');
  // Literal keys only. Template keys (`severity.${id}`) are enumerated below.
  for (const match of text.matchAll(/\bt\(\s*'([a-z0-9._-]+)'/gi)) used.add(match[1]);
  for (const match of text.matchAll(/\bt\(\s*`([a-z0-9._-]+)\$\{/gi))
    used.add(match[1] + '*');
}

const dict = fs.readFileSync(path.join(SRC, 'i18n', 'index.tsx'), 'utf8');
const sections = dict.split(/^const (\w+): Dictionary = \{$/m);
const dictionaries = {};
for (let i = 1; i < sections.length; i += 2) {
  const keys = new Set();
  for (const match of sections[i + 1].matchAll(/^ {2}'([^']+)':/gm)) keys.add(match[1]);
  dictionaries[sections[i]] = keys;
}

let missingTotal = 0;
for (const [name, keys] of Object.entries(dictionaries)) {
  const missing = [...used].filter((key) => {
    if (key.endsWith('*')) {
      const prefix = key.slice(0, -1);
      return ![...keys].some((k) => k.startsWith(prefix));
    }
    return !keys.has(key);
  });
  missingTotal += missing.length;
  console.log(`${name}: ${missing.length} missing`);
  for (const key of missing.sort()) console.log(`  ${key}`);
}

const unused = Object.values(dictionaries).length
  ? [...Object.values(dictionaries)[0]].filter(
      (key) =>
        !used.has(key) &&
        ![...used].some((u) => u.endsWith('*') && key.startsWith(u.slice(0, -1))),
    )
  : [];
// Advisory only: keys reached through a variable (`t(item.labelKey, …)`) look unused
// here. Worth reading anyway — it is how we caught ErrorState hardcoding its Chinese.
console.log(`\npossibly unused in en: ${unused.length}`);
for (const key of unused.sort()) console.log(`  ${key}`);

process.exit(missingTotal > 0 ? 1 : 0);
