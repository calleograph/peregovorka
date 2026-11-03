// pgcdn.js — модуль njs узла CDN Peregovorka.
// auth   — проверка ссылки зрителя без обращения к основному серверу: /hls/<доступ>/<срок>/<kid>.<подпись>/<файл>,
//          подпись ES256 (P-256, SHA-256) строки "pgcdn1|<доступ>|<срок>" открытым ключом kid из /etc/pgcdn/runtime.json.
//          Проверенная подпись запоминается в общей памяти: ECDSA считается один раз на окно срока, а не на каждый запрос.
// denied — ответ на отказ: 410 (срок вышел — плеер возьмёт новую ссылку), 429 (слишком много неверных подписей с адреса), 403.
// log    — счётчики запросов, попаданий в кеш, байтов, адресов зрителей по эфирам (вызывается из access_log if=, строка в журнал не пишется).
// stats  — JSON для основного сервера (Authorization: Bearer <токен узла>): счётчики, соединения, процессор, память, сеть, кеш.
import fs from 'fs';
import cr from 'crypto';

const RUNTIME = '/etc/pgcdn/runtime.json';
const DISK = '/var/lib/pgcdn/disk.json';
const RE = /^\/hls\/([0-9a-f]{24})\/([0-9]{9,11})\/([0-9a-f]{8})\.([A-Za-z0-9_-]{86})\/(index\.m3u8|seg_[0-9]+\.ts)$/;
const BAD_LIMIT = 60;            // неверных подписей с одного адреса за минуту (зона pgcdn_bad, timeout 60 с), дальше — 429 без вычислений

let rt = null;
const keys = {};                 // kid → CryptoKey (импорт один раз на рабочий процесс)

function runtime() {
    if (rt === null) {
        try { rt = JSON.parse(fs.readFileSync(RUNTIME, 'utf8')); } catch (e) { rt = { keys: {}, api_token_sha256: '' }; }
    }
    return rt;
}

async function key(kid) {
    if (keys[kid]) return keys[kid];
    const spki = runtime().keys[kid];
    if (!spki) return null;
    keys[kid] = await crypto.subtle.importKey('spki', Buffer.from(spki, 'base64'), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
    return keys[kid];
}

function deny(r, reason) {
    r.headersOut['X-Pgcdn-Reason'] = reason;
    r.return(403);
}

async function auth(r) {
    const m = RE.exec(r.variables.request_uri.split('?')[0]);
    if (!m) return deny(r, 'bad');
    if (Number(m[2]) < Date.now() / 1000) return deny(r, 'expired');
    const want = m[1] + '|' + m[2] + '|' + m[3];
    const tok = ngx.shared.pgcdn_tok;
    if (tok.get(m[4]) === want) return r.return(204);
    const ip = r.variables.remote_addr;
    const bad = ngx.shared.pgcdn_bad;
    if ((bad.get(ip) || 0) >= BAD_LIMIT) return deny(r, 'slow');
    let ok = false;
    try {
        const k = await key(m[3]);
        ok = !!k && await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, k, Buffer.from(m[4], 'base64url'), 'pgcdn1|' + m[1] + '|' + m[2]);
    } catch (e) { ok = false; }
    if (!ok) {
        bad.incr(ip, 1, 0);
        ngx.shared.pgcdn_stat.incr('deny_sig', 1, 0);
        return deny(r, 'sig');
    }
    tok.set(m[4], want);
    r.return(204);
}

function denied(r) {
    const why = r.variables.pgcdn_reason || '';
    ngx.shared.pgcdn_stat.incr('deny_' + (why || 'other'), 1, 0);
    const code = why === 'expired' ? 410 : why === 'slow' ? 429 : 403;
    r.headersOut['Cache-Control'] = 'no-store';
    r.headersOut['Access-Control-Allow-Origin'] = '*';
    r.return(code, code === 410 ? 'link expired\n' : code === 429 ? 'too many invalid links\n' : 'forbidden\n');
}

function log(r) {
    const s = ngx.shared.pgcdn_stat;
    const bytes = Number(r.variables.body_bytes_sent) || 0;
    s.incr('req', 1, 0);
    s.incr('bytes', bytes, 0);
    s.incr('st_' + r.variables.status, 1, 0);
    const c = r.variables.upstream_cache_status;
    if (c) s.incr('cache_' + c, 1, 0);
    const up = Number(r.variables.upstream_bytes_received) || 0;
    if (up) s.incr('origin_bytes', up, 0);
    const m = /^\/hls\/([0-9a-f]{24})\//.exec(r.variables.request_uri);
    if (m && r.variables.status < 400) {
        s.incr('acc_' + m[1] + '_bytes', bytes, 0);
        s.incr('acc_' + m[1] + '_req', 1, 0);
        ngx.shared.pgcdn_seen.set(m[1] + '|' + r.variables.remote_addr, '1');
    }
    return '';                   // пусто — access_log с if= ничего не пишет
}

function readNums(path) {
    try { return fs.readFileSync(path, 'utf8'); } catch (e) { return ''; }
}

function sys() {
    const out = {};
    const stat = readNums('/proc/stat').split('\n')[0].trim().split(/\s+/).slice(1).map(Number);
    if (stat.length >= 4) out.cpu = { total: stat.reduce((a, b) => a + b, 0), idle: stat[3] + (stat[4] || 0) };
    const mem = {};
    readNums('/proc/meminfo').split('\n').forEach((l) => { const p = /^(\w+):\s+(\d+)/.exec(l); if (p) mem[p[1]] = Number(p[2]) * 1024; });
    out.mem = { total: mem.MemTotal || 0, available: mem.MemAvailable || 0, cached: mem.Cached || 0 };
    const net = { rx: 0, tx: 0 };
    readNums('/proc/net/dev').split('\n').slice(2).forEach((l) => {
        const p = l.trim().split(/[:\s]+/);
        if (p.length > 9 && p[0] !== 'lo') { net.rx += Number(p[1]); net.tx += Number(p[9]); }
    });
    out['net'] = net;
    out.load = readNums('/proc/loadavg').split(' ').slice(0, 3).map(Number);
    out.cpus = (readNums('/proc/cpuinfo').match(/^processor/gm) || []).length;
    try { out.disk = JSON.parse(fs.readFileSync(DISK, 'utf8')); } catch (e) { out.disk = null; }
    return out;
}

function stats(r) {
    const want = runtime().api_token_sha256 || '';
    const got = (r.headersIn['Authorization'] || '').replace(/^Bearer\s+/i, '');
    if (!want || !got || cr.createHash('sha256').update(got).digest('hex') !== want) {
        r.return(401, 'unauthorized\n');
        return;
    }
    const counters = {};
    ngx.shared.pgcdn_stat.items().forEach((kv) => { counters[kv[0]] = kv[1]; });
    const viewers = {};
    ngx.shared.pgcdn_seen.keys().forEach((k) => { const acc = k.split('|')[0]; viewers[acc] = (viewers[acc] || 0) + 1; });
    const v = r.variables;
    const body = {
        node: runtime().node_id || '', version: runtime().version || '', nginx: ngx.version, njs: njs.version, time: Date.now() / 1000,
        counters, viewers, keys: Object.keys(runtime().keys || {}),
        conn: { active: Number(v.connections_active) || 0, reading: Number(v.connections_reading) || 0, writing: Number(v.connections_writing) || 0, waiting: Number(v.connections_waiting) || 0 },
        sys: sys(),
    };
    r.headersOut['Content-Type'] = 'application/json';
    r.headersOut['Cache-Control'] = 'no-store';
    r.return(200, JSON.stringify(body));
}

export default { auth, denied, log, stats };
