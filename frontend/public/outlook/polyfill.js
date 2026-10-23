/* Peregovorka для Outlook: Promise для встроенного браузера классического Outlook (Internet Explorer 11 — Office 2016/2019 с локальным Exchange),
 * где его нет. Только если Promise отсутствует; в остальных браузерах файл ничего не делает. ES5, без зависимостей.
 * Минимальная реализация по Promises/A+: then/catch, resolve/reject/all/race; продолжения — асинхронно (setTimeout). */
(function (root) {
  if (typeof root.Promise === "function") return;
  var PENDING = 0, FULFILLED = 1, REJECTED = 2;
  function later(fn) { setTimeout(fn, 0); }
  function P(executor) {
    var self = this;
    self._s = PENDING; self._v = undefined; self._q = [];
    function settle(state, value) {
      if (self._s !== PENDING) return;
      self._s = state; self._v = value;
      var q = self._q; self._q = [];
      for (var i = 0; i < q.length; i++) handle(self, q[i]);
    }
    function resolve(x) {
      if (x === self) { settle(REJECTED, new TypeError("цикл в Promise")); return; }
      if (x && (typeof x === "object" || typeof x === "function")) {
        var then, called = false;
        try { then = x.then; } catch (e) { settle(REJECTED, e); return; }
        if (typeof then === "function") {
          try {
            then.call(x, function (y) { if (!called) { called = true; resolve(y); } }, function (r) { if (!called) { called = true; settle(REJECTED, r); } });
          } catch (e) { if (!called) { called = true; settle(REJECTED, e); } }
          return;
        }
      }
      settle(FULFILLED, x);
    }
    self._resolve = resolve;
    try { executor(resolve, function (r) { settle(REJECTED, r); }); } catch (e) { settle(REJECTED, e); }
  }
  function handle(p, h) {
    if (p._s === PENDING) { p._q.push(h); return; }
    later(function () {
      var cb = p._s === FULFILLED ? h.ok : h.err;
      if (typeof cb !== "function") { if (p._s === FULFILLED) h.resolve(p._v); else h.reject(p._v); return; }
      var r;
      try { r = cb(p._v); } catch (e) { h.reject(e); return; }
      h.resolve(r);
    });
  }
  P.prototype.then = function (ok, err) {
    var self = this;
    return new P(function (resolve, reject) { handle(self, { ok: ok, err: err, resolve: resolve, reject: reject }); });
  };
  P.prototype["catch"] = function (err) { return this.then(null, err); };
  P.resolve = function (v) { return v instanceof P ? v : new P(function (r) { r(v); }); };
  P.reject = function (e) { return new P(function (_, r) { r(e); }); };
  P.all = function (list) {
    return new P(function (resolve, reject) {
      var out = [], left = list.length;
      if (!left) { resolve(out); return; }
      for (var i = 0; i < list.length; i++) {
        (function (i) { P.resolve(list[i]).then(function (v) { out[i] = v; if (--left === 0) resolve(out); }, reject); })(i);
      }
    });
  };
  P.race = function (list) {
    return new P(function (resolve, reject) { for (var i = 0; i < list.length; i++) P.resolve(list[i]).then(resolve, reject); });
  };
  root.Promise = P;
})(typeof window !== "undefined" ? window : this);
