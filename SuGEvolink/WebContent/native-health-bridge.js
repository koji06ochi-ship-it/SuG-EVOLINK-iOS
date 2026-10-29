/* S.u.G EVOLINK: native HealthKit adapter. No remote transmission; no automatic localStorage writes. */
(function () {
  'use strict';
  var latest = null;
  var accepted = ['steps','distanceKm','activeEnergyKcal','sleepHours','heartRate','restingHeartRate','hrvMs'];
  var labels = {steps:'歩数',distanceKm:'距離 km',activeEnergyKcal:'活動消費 kcal',sleepHours:'睡眠 時間',heartRate:'心拍 bpm',restingHeartRate:'安静時心拍 bpm',hrvMs:'HRV ms'};
  function text(value) { return typeof value === 'string' ? value.slice(0,512) : ''; }
  function date(value) { return typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : ''; }
  function metadata(value) {
    value = value && typeof value === 'object' ? value : {};
    return Object.freeze({kind:value.kind==='sample'?'sample':'period', label:text(value.label),
      observedStart:date(value.observedStart), observedEnd:date(value.observedEnd),
      periodStart:date(value.periodStart), periodEnd:date(value.periodEnd), timeZone:text(value.timeZone),
      aggregation:text(value.aggregation), userEntered:typeof value.userEntered==='boolean'?value.userEntered:null,
      sources:Object.freeze((Array.isArray(value.sources)?value.sources:[]).slice(0,20).map(function(s){return Object.freeze({name:text(s?.name),bundleIdentifier:text(s?.bundleIdentifier)});} ))});
  }
  function receive(payload) {
    if (!payload || payload.source !== 'healthkit_native_evolink' || payload.verifiedMeasurement !== true || !payload.metrics) return false;
    var metrics = {};
    accepted.forEach(function (key) {
      var n = payload.metrics[key];
      if (typeof n === 'number' && Number.isFinite(n) && n >= 0) metrics[key] = n;
    });
    if (!Object.keys(metrics).length) return false;
    var meta = {};
    Object.keys(metrics).forEach(function(key){meta[key]=metadata(payload.metadata?.[key]);});
    latest = Object.freeze({source:payload.source, syncedAt:date(payload.syncedAt), metrics:Object.freeze(metrics), metadata:Object.freeze(meta)});
    window.dispatchEvent(new CustomEvent('sug:evolink-native-health', {detail:latest}));
    render();
    return true;
  }
  function clear() {
    latest = null;
    var old = document.getElementById('sug-evolink-health-inline');
    if (old) old.remove();
    window.dispatchEvent(new CustomEvent('sug:evolink-native-health-cleared', {detail:null}));
  }
  function render() {
    var target = document.getElementById('mainContent');
    if (!target) return;
    var inScope = /\b(ADAPT|TRAINING|NUTRITION)\b/i.test(document.title || '');
    var old = document.getElementById('sug-evolink-health-inline');
    if (!inScope) {if (old) old.remove(); return;}
    if (!latest) return;
    var signature = (document.title || "") + "|" + JSON.stringify(latest);
    if (old && old.dataset && old.dataset.signature === signature) return;
    if (!old) {
      old = document.createElement('section'); old.id='sug-evolink-health-inline';
      old.style.cssText='margin:12px 0;padding:12px;border:1px solid #b99854;border-radius:12px;background:#0b302b;color:#f5e6c1;font:13px/1.65 sans-serif';
      target.insertBefore(old, target.firstChild);
    }
    if (old.dataset) old.dataset.signature = signature;
    old.replaceChildren();
    var h = document.createElement('strong'); h.textContent='Appleヘルスケア｜取得値'; old.appendChild(h);
    var p = document.createElement('p');
    p.textContent = '取得時刻：' + (latest.syncedAt || '未提供（測定時刻とは別です）');
    old.appendChild(p);
    Object.entries(latest.metrics).forEach(function(pair){
      var key=pair[0], m=latest.metadata[key], row=document.createElement('div');
      row.style.cssText='padding:10px 0;border-top:1px solid #b9985444;overflow-wrap:anywhere';
      var value=document.createElement('strong');value.textContent=(labels[key]||key)+'：'+pair[1];row.appendChild(value);
      var detail=document.createElement('p');detail.style.cssText='font-size:11px;margin:5px 0';
      var start=m.kind==='sample'?m.observedStart:m.periodStart, end=m.kind==='sample'?m.observedEnd:m.periodEnd;
      detail.textContent=(m.label?m.label+' ／ ':'')+(m.kind==='sample'?'測定記録：':'集計期間：')+(start||'未提供')+' ～ '+(end||'未提供')+
        (m.timeZone?' ／ '+m.timeZone:'')+' ／ 取得元：'+(m.sources.map(function(s){return s.name+(s.bundleIdentifier?' ('+s.bundleIdentifier+')':'');}).filter(Boolean).join('、')||'未提供')+
        (m.userEntered===null?'':' ／ 手入力フラグ：'+(m.userEntered?'あり':'なし'));
      row.appendChild(detail);old.appendChild(row);
    });
    var note=document.createElement('small');
    note.textContent='HealthKitの保存記録です。センサー実測の保証ではありません。既存の運動負荷・栄養計算を自動変更しません。'; old.appendChild(note);
  }
  window.SuGEvolinkNativeHealth = Object.freeze({receive:receive, clear:clear, snapshot:function(){return latest;}, requestRefresh:function(){
    if(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.sugEvolinkHealth)
      window.webkit.messageHandlers.sugEvolinkHealth.postMessage({action:'refresh'});
  }});
  var observer = new MutationObserver(function(){render();});
  function start(){observer.observe(document.documentElement,{childList:true,subtree:true});render();}
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',start);
  else start();
})();
