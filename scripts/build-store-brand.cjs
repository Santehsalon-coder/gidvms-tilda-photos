'use strict';
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const crypto = require('node:crypto');
const { gidStoreCategory, gidStoreMembership } = require('./store-brand-membership.cjs');

const directory = path.resolve(process.argv[2] || 'catalog/ready536');
const load = file => JSON.parse(fs.readFileSync(path.join(directory, file), 'utf8'));
function atomicWrite(file, bytes) {
  const destination = path.join(directory, file);
  fs.writeFileSync(destination + '.tmp', bytes);
  fs.renameSync(destination + '.tmp', destination);
}
const generation = load('generation.json');
if (generation.schema !== 536 || !Number.isSafeInteger(generation.at) || !Object.keys(generation.parts).length)
  throw new Error('Unsupported catalogue generation');
const products = new Map();
for (const [part, expected] of Object.entries(generation.parts)) {
  if (!/^\d+$/.test(part)) throw new Error('Invalid part identifier');
  const snapshot = load(part + '.json');
  if (snapshot.schema !== 536 || String(snapshot.part) !== part || snapshot.total !== expected)
    throw new Error('Incomplete category ' + part);
  const seen = new Set();
  for (const chunk of snapshot.chunks) {
    const bytes = zlib.inflateRawSync(Buffer.from(chunk.data, 'base64'), {maxOutputLength: 2000000});
    if (bytes.length !== chunk.size) throw new Error('Incomplete category chunk');
    for (const product of JSON.parse(bytes)) {
      const id = String(product.uid);
      if (!/^[1-9]\d+$/.test(id) || seen.has(id)) throw new Error('Invalid product in ' + part);
      seen.add(id);
      if (!products.has(id)) products.set(id, {product, parts: []});
      products.get(id).parts.push(part);
    }
  }
  if (seen.size !== expected) throw new Error('Count mismatch in ' + part);
}
// Published products may live in newly created sections or only in the root.
if (process.argv[3]) {
  const extras = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
  if (!Array.isArray(extras)) throw new Error('Invalid additional product list');
  for (const product of extras) {
    const id = String(product.uid);
    if (!/^[1-9]\d+$/.test(id)) throw new Error('Invalid additional UID');
    if (products.has(id)) continue;
    const raw = product.partuids || [];
    const parts = Array.isArray(raw) ? raw.map(String) : (String(raw).match(/\d+/g) || []);
    products.set(id, {product, parts});
  }
}
const categories = {bath: [], furniture: [], mirrors: [], kits: []};
const keys = new Map([['Ванны','bath'],['Мебель для ванной','furniture'],['Зеркала с подсветкой','mirrors'],['Инсталляции с унитазом','kits']]);
const own = [], byInstallation = Object.create(null), chunks = [];
let pending = [], size = 2;
function flush() {
  if (!pending.length) return;
  const bytes = Buffer.from('[' + pending.join(',') + ']');
  chunks.push({count: pending.length, size: bytes.length, data: zlib.deflateRawSync(bytes, {level: 9}).toString('base64')});
  pending = []; size = 2;
}
for (const id of [...products.keys()].sort()) {
  const {product,parts} = products.get(id);
  const member = gidStoreMembership(product);
  if (!member.own) continue;
  own.push(id);
  const key = keys.get(gidStoreCategory(product, undefined, undefined, parts));
  if (key) categories[key].push(id);
  if (member.kit && member.installer) {
    const brand = member.installer.trim().toLowerCase().normalize('NFC');
    (byInstallation[brand] ||= []).push(id);
  }
  const row = JSON.stringify(product), length = Buffer.byteLength(row) + 1;
  if (length + 2 > 2000000) throw new Error('Oversize product ' + id);
  if (size + length > 2000000) flush();
  pending.push(row); size += length;
}
flush();
const content = Buffer.from(JSON.stringify({schema:536,encoding:'deflate-raw-base64-chunks',total:own.length,chunks}));
const sha256 = crypto.createHash('sha256').update(content).digest('hex');
const rows = {file:'store-brands/' + sha256 + '.json', sha256};
const indexFile = 'store-brand.json';
const previous = fs.existsSync(path.join(directory,indexFile)) ? load(indexFile) : null;
const previousFile = previous?.rows?.file === rows.file ? previous.previousFile : previous?.rows?.file;
const index = {schema:1,at:generation.at,own,byInstallation,categories,rows};
if (previousFile && /^store-brands\/[0-9a-f]{64}\.json$/.test(previousFile)) index.previousFile = previousFile;
fs.mkdirSync(path.join(directory,'store-brands'),{recursive:true});
if (!fs.existsSync(path.join(directory,rows.file))) atomicWrite(rows.file,content);
atomicWrite(indexFile,JSON.stringify(index));
for (const filename of fs.readdirSync(path.join(directory,'store-brands'))) {
  const relative = 'store-brands/' + filename;
  if (/^[0-9a-f]{64}\.json$/.test(filename) && relative !== rows.file && relative !== index.previousFile)
    fs.unlinkSync(path.join(directory,relative));
}
console.log(JSON.stringify({catalogue:products.size,own:own.length,categories:Object.fromEntries(Object.entries(categories).map(([key,value])=>[key,value.length])),kitManufacturers:Object.fromEntries(Object.entries(byInstallation).map(([key,value])=>[key,value.length])),rowBytes:content.length,sha256}));
