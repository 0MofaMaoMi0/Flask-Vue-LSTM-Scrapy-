import {countryName} from './countryDisplay.js';
import {countryLocation} from './countryLocations.js';
// G2-B independent visual presets. All effects are rendering-only.
// These colors are never allowed to change or override the analytics data.
export const GLOBE_THEMES = Object.freeze({
  cinema: Object.freeze({id:'cinema',label:'影院紫',background:'#0b0d21',atmosphere:'#7950da',stroke:'#6a60a6',side:'rgba(43,24,87,.52)',empty:'#202444',low:['#353567','#554093','#8055c0','#bc78f1'],hover:'#d4a0ff',selected:'#efb6ff',label:'#c5b1ee',pulse:['#8b62ea','#ab77fa','#00000000']}),
  noir: Object.freeze({id:'noir',label:'冷灰蓝',background:'#091220',atmosphere:'#6b8ab8',stroke:'#526b90',side:'rgba(30,46,71,.42)',empty:'#1f2c3f',low:['#324967','#446588','#5b82b7','#8ba7d9'],hover:'#b8d6ff',selected:'#e0eafa',label:'#b8c8e5',pulse:['#6c98c7','#8cb8de','#00000000']}),
  aurora: Object.freeze({id:'aurora',label:'星雾紫',background:'#100c28',atmosphere:'#a364e6',stroke:'#8162b7',side:'rgba(60,29,105,.55)',empty:'#272043',low:['#503674','#71469e','#9960ce','#d987ed'],hover:'#ffc1f8',selected:'#ffcaf5',label:'#dba9f1',pulse:['#a565da','#df89fc','#00000000']})
});
const LIGHT_GLOBE_THEMES = Object.freeze({
  cinema: Object.freeze({id:'cinema',label:'影院紫',background:'#F7F4EE',atmosphere:'#8C6226',stroke:'#8C6226',side:'rgba(140,98,38,.28)',empty:'#DFD7C8',low:['#E6D5B8','#CFAF7C','#AF8242','#8C6226'],hover:'#754F1B',selected:'#1F1B16',label:'#1F1B16',pulse:['#8C6226','#B58742','#00000000']}),
  noir: Object.freeze({id:'noir',label:'冷灰蓝',background:'#F7F4EE',atmosphere:'#5B728E',stroke:'#526B90',side:'rgba(82,107,144,.25)',empty:'#DCE3EC',low:['#CFDCEB','#9EB6D2','#6D8EB5','#446588'],hover:'#2F4C70',selected:'#1F1B16',label:'#1F1B16',pulse:['#5B82B7','#8BA7D9','#00000000']}),
  aurora: Object.freeze({id:'aurora',label:'星雾紫',background:'#F7F4EE',atmosphere:'#8D62A6',stroke:'#7E5A9E',side:'rgba(126,90,158,.25)',empty:'#E4DCEB',low:['#DDD0E8','#BD9ED6','#9970BA','#724994'],hover:'#543270',selected:'#1F1B16',label:'#1F1B16',pulse:['#8D62A6','#B58AD1','#00000000']})
});
export const GLOBE_EFFECTS=Object.freeze({subtle:'克制',balanced:'平衡',vivid:'强调'});
export function pickTheme(id){
  const isLight=typeof document!=='undefined'&&document.documentElement?.dataset?.theme==='light';
  const map=isLight?LIGHT_GLOBE_THEMES:GLOBE_THEMES;
  return map[id]||map.cinema;
}
export function intensity(stat,metric,maxValue=1){
  if(!stat)return null;
  const value=stat[metric];
  if(!Number.isFinite(value)||(metric!=='average_rating'&&value<=0))return null;
  return metric==='average_rating'?Math.max(0,Math.min(1,(value-1)/4)):Math.max(0,Math.min(1,value/Math.max(1,maxValue)));
}
export function shade(stat,metric,maxValue,themeId='cinema',hovered=false,selected=false){
  const p=pickTheme(themeId);
  if(selected)return p.selected;
  if(hovered)return p.hover;
  const factor=intensity(stat,metric,maxValue);
  if(factor===null)return p.empty;
  return p.low[Math.min(3,Math.floor(factor*4))];
}
export function altitude({selected=false,hovered=false,effort='balanced'}={}){
  const n=effort==='subtle'?.78:effort==='vivid'?1.23:1;
  return selected?.116*n:hovered?.085*n:.025*n;
}
export function spotlightData(stats,metric,features,selectedIso3='',limit=4){
  const featureMap=new Map(features.map(f=>[f.properties.iso3,f]));
  const eligible=stats.countries.filter(x=>Number.isFinite(x[metric])&&x[metric]>0&&featureMap.has(x.iso3))
    .sort((a,b)=>b[metric]-a[metric]||a.iso3.localeCompare(b.iso3));
  const chosen=[];
  const selected=stats.countries.find(x=>x.iso3===selectedIso3);
  // Selected country has priority; surrounding small countries must not create overlapping labels.
  if(selected&&featureMap.has(selectedIso3))chosen.push(selected);
  for(const row of eligible){
    if(chosen.some(x=>x.iso3===row.iso3))continue;
    if(chosen.length>=(limit+(chosen.length&&chosen[0].iso3===selectedIso3?1:0)))break;
    const point=featureMap.get(row.iso3).properties;
    if(chosen.some(prev=>{
      const p=featureMap.get(prev.iso3).properties;
      const dx=(point.lng-p.lng)*Math.cos((point.lat+p.lat)/2*Math.PI/180);
      return Math.hypot(point.lat-p.lat,dx)<14;
    }))continue;
    chosen.push(row);
  }
  return chosen.map(row=>{
    const point=featureMap.get(row.iso3).properties;
    const selected= row.iso3===selectedIso3;
    const value=metric==='average_rating' ? row.average_rating?.toFixed(1) : row[metric];
    const suffix=metric==='average_rating'?'分':'部';
    return {iso3:row.iso3, lat:point.lat,lng:point.lng,selected,
      shortName:row.iso3, fullName:countryName(row.iso3,point.name),
      countLabel:`${value ?? '—'}${suffix}`,
      text:`${row.iso3} ${value ?? '—'}`};
  });
}
/** Explicit checkbox-driven labels. No automatic top-N, hover, or selected label. */
export function manualLabelData(stats, metric, features, checkedIso3 = [], selectedIso3 = '') {
  const featureMap = new Map(features.map(f => [f.properties.iso3, f]));
  const records = new Map((stats?.countries || []).map(c => [c.iso3, c]));
  const codes = Array.isArray(checkedIso3) ? checkedIso3 : [];
  return [...new Set(codes)].filter(code => typeof code === 'string' && countryLocation(code,featureMap))
    .map(iso3 => {
      const point = countryLocation(iso3,featureMap);
      const row = records.get(iso3);
      const selected = iso3 === selectedIso3;
      const value = metric === 'average_rating'
        ? (Number.isFinite(row?.average_rating) ? row.average_rating.toFixed(1) : '—')
        : (Number.isFinite(row?.[metric]) ? row[metric] : 0);
      return {iso3, lat:point.lat, lng:point.lng, selected,
        shortName:iso3, fullName:countryName(iso3, point.name),
        countLabel:`${value}${metric === 'average_rating' ? '分' : '部'}`};
    });
}
export function clampCameraAltitude(value){return Math.min(2.3,Math.max(1.52,Number(value)||1.85));}

/** Target globe diameter ~88% of available viewport, without cropping in either axis. */
export function fitGlobeAltitude(width,height,fovDegrees=50,fill=.88){
  const w=Math.max(1,Number(width)||1), h=Math.max(1,Number(height)||1);
  const fov=Math.min(100,Math.max(15,Number(fovDegrees)||50))*Math.PI/180;
  const targetDiameter=Math.max(1,Math.min(w,h)*Math.max(.45,Math.min(.94,Number(fill)||.88)));
  const distanceInRadii=h/(Math.tan(fov/2)*targetDiameter);
  return Math.min(4.7,Math.max(.55,distanceInRadii-1));
}
