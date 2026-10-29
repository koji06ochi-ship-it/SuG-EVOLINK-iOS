/* Local JSON export bridge. Native Files picker only after explicit user action. */
(function () {
  'use strict';
  var pending = new Map(), serial = 0;
  function available() { return !!window.webkit?.messageHandlers?.sugEvolinkFiles; }
  function exportJSON(name, json) {
    if (!available()) return Promise.reject(new Error('iOSのファイル書出しに接続されていません'));
    if (typeof json !== 'string' || new TextEncoder().encode(json).length > 10 * 1024 * 1024)
      return Promise.reject(new Error('JSONは10MB以下にしてください'));
    try { var data = JSON.parse(json); if (!data || typeof data !== 'object' || Array.isArray(data)) throw Error(); }
    catch (_) { return Promise.reject(new Error('バックアップJSONの形式が不正です')); }
    if (pending.size) return Promise.reject(new Error('先の書出し操作を終了してください'));
    var id = 'export-' + Date.now() + '-' + (++serial);
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { pending.delete(id); reject(new Error('書出しの完了を確認できませんでした')); }, 300000);
      pending.set(id, {resolve:resolve, reject:reject, timer:timer});
      try { window.webkit.messageHandlers.sugEvolinkFiles.postMessage({action:'exportJSON', id:id, name:String(name), json:json}); }
      catch (_) { finish(id, 'failed', 'ファイル書出しを開始できませんでした'); }
    });
  }
  function finish(id, status, message) {
    var job = pending.get(id); if (!job) return false;
    pending.delete(id); clearTimeout(job.timer);
    if (status === 'exported' || status === 'cancelled') job.resolve({status:status});
    else job.reject(new Error(message || 'ファイル書出しに失敗しました'));
    return true;
  }
  window.SuGEvolinkNativeFiles = Object.freeze({available:available, exportJSON:exportJSON, finish:finish});
})();
