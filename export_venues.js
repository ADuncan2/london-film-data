// Writes every Clusterflick venue record (clusterflick/scripts cinemas/*/attributes.js) to one JSON file.
//   node export_venues.js <path to scripts/cinemas> <output .json>
const fs = require("fs");
const path = require("path");
const [dir, out] = process.argv.slice(2);
if (!dir || !out) {
  console.error("usage: node export_venues.js <scripts/cinemas> <venues.json>");
  process.exit(2);
}
const venues = {};
for (const name of fs.readdirSync(dir).sort()) {
  const file = path.resolve(dir, name, "attributes.js");
  if (!fs.existsSync(file)) continue;
  const a = require(file);
  venues[a.id || name] = a;
}
fs.writeFileSync(out, JSON.stringify(venues, null, 1));
console.log(`${Object.keys(venues).length} venues -> ${out}`);
