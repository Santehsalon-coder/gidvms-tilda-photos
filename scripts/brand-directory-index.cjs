'use strict';

const definitions = require('./brand-directory-config.json');
const { gidStoreMembership } = require('./store-brand-membership.cjs');
const ownSlug = 'gid-v-mire-santehniki';

function normalize(value) {
  return String(value || '').toLowerCase().replace(/ё/g, 'е').replace(/&amp;/g, '&')
    .replace(/[«»"']/g, '').trim().replace(/\s+/g, ' ');
}

function wholeWords(value) {
  return ' ' + normalize(value).replace(/[^\p{L}\p{N}]+/gu, ' ').trim() + ' ';
}

const aliases = new Map();
const proofByUid = new Map();
for (const [slug, definition] of Object.entries(definitions)) {
  for (const label of definition.labels) {
    const key = normalize(label);
    if (aliases.has(key) && aliases.get(key) !== slug) throw Error('Ambiguous brand: ' + label);
    aliases.set(key, slug);
  }
  for (const [uid, token] of Object.entries(definition.proof || {})) {
    if (proofByUid.has(uid)) throw Error('Ambiguous product: ' + uid);
    proofByUid.set(uid, {slug, token});
  }
}

function verifiedBrand(uid, title) {
  const proof = proofByUid.get(uid);
  if (!proof) return;
  const name = wholeWords(title);
  const term = wholeWords(proof.token);
  if (name.includes(term) && ![' для',' аналог',' совместим с'].some(prefix => name.includes(prefix + term))) {
    return proof.slug;
  }
}

function firstPhoto(product) {
  let gallery = product.gallery;
  if (typeof gallery === 'string') {
    try { gallery = JSON.parse(gallery); } catch { return ''; }
  }
  if (!Array.isArray(gallery)) return '';
  for (const item of gallery) {
    const url = typeof item === 'string' ? item : item?.img;
    if (typeof url === 'string' && /^https:\/\//i.test(url)) return url;
  }
  return '';
}

function brandDirectoryIndex(products, generation) {
  const brands = Object.fromEntries(Object.keys(definitions).map(key => [key, {count: 0, parts: []}]));
  const approved = new Map(Object.entries(definitions).filter(([, value]) => value.photos).map(([key]) => [key, new Set()]));
  const parts = new Map(Object.keys(definitions).map(key => [key, new Set()]));
  function add(slug, uid, product, productParts) {
    if (!slug) return;
    const entry = brands[slug];
    entry.count++;
    if (definitions[slug].photos?.includes(uid)) approved.get(slug).add(uid);
    for (const part of productParts) parts.get(slug).add(String(part));
    if (entry.image) return;
    const image = firstPhoto(product);
    if (!entry.uid || image) entry.uid = uid;
    if (image) entry.image = image;
  }
  for (const uid of [...products.keys()].sort()) {
    const {product, parts: productParts} = products.get(uid);
    const member = gidStoreMembership(product);
    if (member.own) add(ownSlug, uid, product, productParts);
    const manufacturer = member.kit ? member.installer : member.manufacturer;
    const slug = manufacturer ? aliases.get(normalize(manufacturer)) : !member.kit && verifiedBrand(uid, product.title);
    if (slug && slug !== ownSlug) add(slug, uid, product, productParts);
  }
  for (const [slug, live] of approved) brands[slug].photoUids = definitions[slug].photos.filter(uid => live.has(uid));
  for (const [slug, partSet] of parts) brands[slug].parts = [...partSet].sort();
  return {schema: 1, generation: generation.at, products: products.size, brands};
}

module.exports = {brandDirectoryIndex};
