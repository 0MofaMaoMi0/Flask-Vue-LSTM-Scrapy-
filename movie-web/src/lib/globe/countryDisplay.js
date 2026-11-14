import {ZH_NAMES} from './countryNamesZh.js';
import {FLAG_ISO3} from 'virtual:flag-isos';
// G2-B.1. The offline SVG icon set covers the 11 countries present in demo data.
// Unknown countries have neutral ISO code fallback rather than an incorrect flag.
const FLAG_ISO3_SET = new Set(FLAG_ISO3);
export const COUNTRY_META = Object.freeze({
  USA:{name:'美国',english:'United States',iso2:'US'}, JPN:{name:'日本',english:'Japan',iso2:'JP'},
  CHN:{name:'中国',english:'China',iso2:'CN'}, GBR:{name:'英国',english:'United Kingdom',iso2:'GB'},
  KOR:{name:'韩国',english:'South Korea',iso2:'KR'}, FRA:{name:'法国',english:'France',iso2:'FR'},
  DEU:{name:'德国',english:'Germany',iso2:'DE'}, IND:{name:'印度',english:'India',iso2:'IN'},
  ITA:{name:'意大利',english:'Italy',iso2:'IT'}, AUS:{name:'澳大利亚',english:'Australia',iso2:'AU'},
  CAN:{name:'加拿大',english:'Canada',iso2:'CA'},
  NZL:{name:'新西兰',english:'New Zealand',iso2:'NZ'}
});
export function countryName(iso3, fallback='') {return COUNTRY_META[iso3]?.name || ZH_NAMES[iso3] || fallback || iso3;}
export function countryEnglish(iso3, fallback='') {return COUNTRY_META[iso3]?.english || fallback || iso3;}
export function flagUrl(iso3) {
  if (!iso3 || !/^[A-Z]{3}$/.test(iso3)) return null;
  const override = typeof window!=='undefined' && window.__MI2_FLAG_ASSETS__?.[iso3];
  if (override) return override;
  return FLAG_ISO3_SET.has(iso3) ? `/m9/globe/flags/${iso3}.svg` : null;
}
export function flagAlt(iso3) {return `国旗：${countryName(iso3)}`;}
