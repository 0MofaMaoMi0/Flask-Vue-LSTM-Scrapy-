import {metricValue, escapeHtml} from './stats.js';
import {GLOBE_THEMES, pickTheme, shade, altitude, manualLabelData, fitGlobeAltitude} from './globeVisuals.js';
import {countryName,countryEnglish,flagUrl,flagAlt} from './countryDisplay.js';

// True Globe.GL engine; no 2D rendering fallback is disguised as WebGL.
export function createMovieGlobe({element, GlobeConstructor, features, getStats, getMetric, getSelected,
  onHover=()=>{}, onSelect=()=>{}, textureUrl='/m9/globe/assets/mi2-earth-g2b.jpg',
  initialView={lat:24,lng:80}, themeId='cinema', effects='vivid', labelIso3=[], initialAutoRotate=true}){
  if(!element)throw new Error('Missing globe DOM mount');
  const globe=new GlobeConstructor(element);
  const env=typeof window==='undefined'?null:window;
  const prefersReducedMotion=!!env?.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  let hoverIso=null;
  let selectedIso=getSelected()||null;
  let currentTheme=themeId in GLOBE_THEMES?themeId:'cinema';
  let currentEffects=effects;
  let checkedLabelIso3=[...new Set(labelIso3)];
  let autoRotate=!!initialAutoRotate;
  let active=true;
  let resizeObserver=null;
  let fitAltitude=1.42;
  let lastWidth=0,lastHeight=0;
  let fittedOnce=false;
  const featureMap=new Map(features.map(f=>[f.properties.iso3,f]));
  const table=()=>new Map(getStats().countries.map(x=>[x.iso3,x]));
  const maxVal=()=>Math.max(1,...getStats().countries.map(c=>metricValue(c,getMetric())||0));
  const getKey=f=>f.properties.iso3;
  const color=f=>shade(table().get(getKey(f)),getMetric(),maxVal(),currentTheme,hoverIso===getKey(f),selectedIso===getKey(f));
  const depth=f=>altitude({selected:getKey(f)===selectedIso,hovered:getKey(f)===hoverIso,effort:currentEffects});
  const frame=f=>getKey(f)===selectedIso?pickTheme(currentTheme).selected:getKey(f)===hoverIso?pickTheme(currentTheme).hover:pickTheme(currentTheme).stroke;
  const flagElement=iso3=>{
    const url=flagUrl(iso3);
    if(url)return `<img class="country-flag" src="${url}" alt="${escapeHtml(flagAlt(iso3))}" width="30" height="20" onerror="this.style.display='none'">`;
    return `<span class="country-code-flag" aria-label="国家代码">${escapeHtml(iso3)}</span>`;
  };
  const tip=f=>{
    const {iso3,name}=f.properties;
    const stat=table().get(iso3);
    const cn=countryName(iso3,name);
    const english=countryEnglish(iso3,name);
    const rated=stat?.average_rating==null?'暂无个人评分':stat.average_rating.toFixed(2)+' / 5';
    return `<div class="mi2-tooltip"><div class="mi2-tooltip-head">${flagElement(iso3)}<div><strong>${escapeHtml(cn)}</strong><span>${escapeHtml(english)} · ${escapeHtml(iso3)}</span></div></div><div class="mi2-tooltip-metrics"><span>收录 <b>${stat?.catalog_count??0} 部</b></span><span>个人已看 <b>${stat?.watched_count??0} 部</b></span></div><div class="mi2-tooltip-rating">个人平均评分 <b>${rated}</b></div><div class="mi2-tooltip-note">制作国家归属 · 全库口径</div></div>`;
  };
  function makeLabel(d){
    const node=document.createElement('div');
    node.className='mi2-map-label'+(d.selected?' is-selected':'');
    node.setAttribute('role','button');
    node.setAttribute('aria-label',`${d.fullName} ${d.countLabel}，选择国家`);
    node.tabIndex=0;
    const url=flagUrl(d.iso3);
    if(url){
      const img=document.createElement('img');img.className='map-label-flag';img.src=url;
      img.alt='';img.width=27;img.height=18;img.draggable=false;img.onerror=()=>{const s=document.createElement('span');s.className='country-code-flag';s.textContent=d.iso3;img.replaceWith(s)};node.appendChild(img);
    }else{
      const fallback=document.createElement('span');fallback.className='country-code-flag';fallback.textContent=d.iso3;node.appendChild(fallback);
    }
    const title=document.createElement('span');title.className='map-label-title';title.textContent=d.selected?d.fullName:d.shortName;
    const count=document.createElement('strong');count.className='map-label-count';count.textContent=d.countLabel;
    node.append(title,count);
    const choose=event=>{event.stopPropagation();onSelect(d.iso3);};
    node.addEventListener('click',choose);
    node.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(event);}});
    return node;
  }
  function applyLabels(){
    const tags=manualLabelData(getStats(),getMetric(),features,checkedLabelIso3,selectedIso);
    globe.htmlElementsData(tags);
    const selected=selectedIso&&featureMap.get(selectedIso);
    globe.ringsData(selected&&currentEffects!=='subtle'?[{
      lat:selected.properties.lat,lng:selected.properties.lng,iso3:selectedIso
    }]:[]);
  }
  globe
    .backgroundColor('rgba(0,0,0,0)')
    .backgroundImageUrl(null)
    .globeImageUrl(textureUrl)
    .showAtmosphere(true)
    .atmosphereColor(pickTheme(currentTheme).atmosphere)
    .atmosphereAltitude(.12)
    .showGraticules(false)
    .lineHoverPrecision(0)
    .polygonsData(features)
    .polygonCapColor(color)
    .polygonSideColor(()=>pickTheme(currentTheme).side)
    .polygonStrokeColor(frame)
    .polygonAltitude(depth)
    .polygonLabel(tip)
    .polygonCapCurvatureResolution(4)
    .polygonsTransitionDuration(prefersReducedMotion?0:340)
    .onPolygonHover(f=>{hoverIso=f?.properties?.iso3||null;onHover(hoverIso);globe.polygonCapColor(color).polygonStrokeColor(frame).polygonAltitude(depth);})
    .onPolygonClick(f=>{if(!f)return;onSelect(getKey(f));})
    .htmlLat('lat').htmlLng('lng').htmlAltitude(d=>d.selected?.21:.13)
    .htmlElement(makeLabel).htmlTransitionDuration(prefersReducedMotion?0:240)
    .ringLat('lat').ringLng('lng').ringAltitude(.055).ringMaxRadius(2.3)
    .ringColor(()=>pickTheme(currentTheme).pulse)
    .ringPropagationSpeed(.75).ringRepeatPeriod(prefersReducedMotion?0:1600)
    .ringResolution(40);
  applyLabels();
  // First fitted view is applied after the canvas receives its measured dimensions.
  const controls=globe.controls();
  controls.enablePan=false;
  controls.enableDamping=true;
  controls.dampingFactor=.058;
  controls.rotateSpeed=.52;
  controls.zoomSpeed=.6;
  controls.minDistance=145;
  controls.maxDistance=730;
  controls.autoRotateSpeed=.28;
  controls.autoRotate=autoRotate&&!prefersReducedMotion;
  function resize(){
    if(!active)return;
    const w=element.clientWidth,h=element.clientHeight;
    if(w<=0||h<=0)return;
    const changed=Math.abs(w-lastWidth)>2||Math.abs(h-lastHeight)>2;
    if(!changed && fittedOnce)return;
    lastWidth=w;lastHeight=h;
    globe.width(w).height(h);
    const fov=typeof globe.camera==='function' ? globe.camera()?.fov : 50;
    fitAltitude=fitGlobeAltitude(w,h,fov,.88);
    if(!fittedOnce || (!selectedIso && changed)) {
      globe.pointOfView({...initialView,altitude:fitAltitude},fittedOnce&&!prefersReducedMotion?280:0);
    }
    fittedOnce=true;
  }
  if(typeof ResizeObserver!=='undefined'){
    resizeObserver=new ResizeObserver(resize);
    resizeObserver.observe(element);
  }
  resize();
  function refresh(){
    if(!active)return;
    globe.polygonCapColor(color).polygonSideColor(()=>pickTheme(currentTheme).side).polygonAltitude(depth).polygonStrokeColor(frame).polygonLabel(tip);
    applyLabels();
  }
  function selectCountry(iso3,animate=true){
    selectedIso=featureMap.has(iso3)?iso3:null;
    controls.autoRotate=false;
    refresh();
    const f=featureMap.get(selectedIso);
    if(f&&animate){
      globe.pointOfView({lat:f.properties.lat,lng:f.properties.lng,altitude:Math.max(.55,fitAltitude*.94)},prefersReducedMotion?0:900);
    }
  }
  function reset(){
    selectedIso=null;hoverIso=null;
    refresh();
    globe.pointOfView({...initialView,altitude:fitAltitude},prefersReducedMotion?0:780);
    controls.autoRotate=autoRotate&&!prefersReducedMotion;
  }
  function setAutoRotate(value){autoRotate=!!value;controls.autoRotate=autoRotate&&!prefersReducedMotion;}
  function setVisuals({themeId,effects}={}){
    if(themeId in GLOBE_THEMES)currentTheme=themeId;
    if(['subtle','balanced','vivid'].includes(effects))currentEffects=effects;

    globe.backgroundColor(pickTheme(currentTheme).background)
      .atmosphereColor(pickTheme(currentTheme).atmosphere)
      .atmosphereAltitude(currentEffects==='subtle'?.09:currentEffects==='vivid'?.17:.12);
    refresh();
  }
  function setLabelIso3(codes=[]){checkedLabelIso3=[...new Set(Array.isArray(codes)?codes:[])];applyLabels();}
  function dispose(){
    if(!active)return;
    active=false;
    resizeObserver?.disconnect();
    controls.autoRotate=false;
    try{globe.pauseAnimation()}catch{}
    try{controls.dispose()}catch{}
    try{globe.renderer().dispose();globe.renderer().forceContextLoss?.()}catch{}
    element.replaceChildren();
  }
  return {globe,refresh,selectCountry,reset,setAutoRotate,setVisuals,setLabelIso3,resize,dispose};
}
