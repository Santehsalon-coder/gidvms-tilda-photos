const GID_STORE_TITLE = "Гид в мире сантехники";
// These legacy store-assembled sets were checked by UID against both the
// original assortment and the published product. New sets use their attributes.
const GID_STORE_KITS = new Map(Object.entries({
  "Grohe": "107067694213 120772536753 122793252593 131380748613 134573161173 139252807913 140314104403 173120870123 227669431113 264107382073 312651897193 334566425693 335386257823 357183198943 360281960573 376399725853 386845466863 395219950063 409634982863 425668674813 445028102523 504789109993 544194751463 556181837283 556802954623 574970419323 575581188793 577257730503 623742511513 637592611133 648106685983 667566885693 699560868833 718848039173 727494141923 744955873913 780832840023 826998463253 858246803443 880862175313 882084565183 892519488383 902261929073 902923450223 903508294473 961924661553 964791760683 974542206083 985161175823",
  "Tece": "167188524933 179325088503 203648825403 222589794663 238660625743 249588909953 254486573503 289084635373 300192638203 301662609013 319769717973 367166506383 399744723313 516150496033 613471423583 917030239233 923737937133 931612427783 992368456893",
  "Oli": "871572637403 903542258933"
}).flatMap(([brand, ids]) => ids.split(" ").map(id => [id, brand])));
function gidStoreAttributes(product, supplied) {
  if (supplied && !Array.isArray(supplied)) return supplied;
  const source = supplied || product.characteristics || [];
  if (!Array.isArray(source)) return source;
  return Object.fromEntries(source.filter(x=>x && x.title).map(x=>[x.title,x.value]));
}
function gidStoreNorm(value) {
  return String(value ?? "").toLowerCase().replace(/ё/g,"е").replace(/&amp;/g,"&").replace(/[«»"']/g,"").trim().replace(/\s+/g," ");
}
function gidStoreMembership(product, attributes) {
  const a=gidStoreAttributes(product,attributes);
  const own=gidStoreNorm(GID_STORE_TITLE);
  function mark(value) {
    const clean=String(value ?? "").trim(), n=gidStoreNorm(clean);
    return !n || /^(?:без бренда|без марки|нет бренда|нет|не указан[ао]?|no brand|no-name|[-—–])$/.test(n) ? "" : clean;
  }
  const marks=[mark(product.brand ?? product.Brand),mark(a["Бренд"]),mark(a["Производитель"])].filter(Boolean);
  const explicit=marks.some(value=>gidStoreNorm(value)===own);
  const manufacturer=marks.find(value=>gidStoreNorm(value)!==own)||"";
  const installer=mark(a["Бренд инсталляции"])||mark(a["Производитель инсталляции"]);
  const component=gidStoreNorm([a["Комплект инсталляции"],a["Тип комплекта"],a["Подкатегория сайта"],a["Подкатегории сайта"],a["Тип товара"]].filter(Boolean).join(" "));
  const complete=/инсталляц/.test(component) && /унитаз/.test(component);
  const name=gidStoreNorm(product.title ?? product.Title);
  const assembler=gidStoreNorm(a["Сборщик комплекта"] || a["Кем собран комплект"]);
  const flag=gidStoreNorm(a["Комплект собран магазином"] ?? a["Комплект магазина"]);
  const byShop=/^(да|yes|true|1)$/.test(flag) || [own,"наш магазин","магазин"].includes(assembler);
  const explicitlyOther=/^(нет|no|false|0)$/.test(flag) || Boolean(assembler && ![own,"наш магазин","магазин"].includes(assembler));
  const partsKnown=complete || (/инсталляц/.test(name) && /унитаз/.test(name));
  const verifiedInstaller=GID_STORE_KITS.get(String(product.uid)) || "";
  const verified=Boolean(verifiedInstaller);
  // Legacy sets may describe the frame in their description while the title only names the pan.
  const legacyTitle=verified && /комплект/.test(name) && /унитаз/.test(name);
  const kit=(partsKnown || legacyTitle) && !explicitlyOther && (byShop || verified);
  return {own:explicit || !manufacturer || kit, kit, installer:kit ? installer || verifiedInstaller : "", manufacturer};
}
function gidStoreCategory(product, attributes, nativeCategory, partIds) {
  const a=gidStoreAttributes(product,attributes);
  if (gidStoreMembership(product,a).kit) return "Инсталляции с унитазом";
  const raw=partIds ?? product.partuids ?? "";
  const parts=new Set((Array.isArray(raw)?raw:String(raw).match(/\d+/g)||[]).map(String));
  const name=gidStoreNorm(product.title ?? product.Title);
  const route=gidStoreNorm([nativeCategory, a["Маршруты каталога"], a["Подкатегория сайта"], a["Подкатегории сайта"], a["Подтип товара"]].filter(Boolean).join(" | "));
  const mirrors=parts.has("496159877623") || /зеркал/.test(name) && /зеркал/.test(route);
  if (mirrors && !/шкаф/.test(name) && !/без подсветки/.test(name+" "+route) &&
      (/^(есть|да|led|светодиодная|фронтальная|парящая)$/.test(gidStoreNorm(a["Подсветка"])) ||
       /с подсветкой|фронтальн.{0,9}подсвет|парящ.{0,9}подсвет|\bled\b/.test(name+" "+route))) return "Зеркала с подсветкой";
  if ((parts.has("338629674673") || /ванны|акриловые ванны|ванна/.test(route)) &&
      /ванн[аы]/.test(name) && !/(экран|каркас|шторк|ограждени|ножк|слив|подголовник|панель|смесител|обвязк|сифон|креплен|ручк[аиу]|раковин|гарнитур|мебель|тумба|шкаф)/.test(name)) return "Ванны";
  if (parts.has("872810302623") || /мебель для ванной|тумбы с раковиной/.test(route)) return "Мебель для ванной";
  return "";
}
module.exports = {gidStoreCategory, gidStoreMembership};
