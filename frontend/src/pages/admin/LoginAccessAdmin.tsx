import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type AccessCheck, type AccessStatus, type ApiError } from "../../api";
import { GroupList } from "./AccessAdmin";

const REASONS: Record<string, string> = {
  not_in_allowed_groups: "не входит ни в одну разрешённую группу",
  connection_not_for_users: "подключение каталога не предназначено для входа пользователей",
  account_disabled: "учётная запись отключена в каталоге",
};
const short = (dn: string) => (dn.match(/^cn=([^,]+)/i)?.[1] ?? dn);

/** «Проверить пользователя»: найден ли в каталоге, пропустят ли правила допуска (по какой группе) и получит ли он права администратора. Пароль не нужен. */
function CheckUser() {
  const [login, setLogin] = useState("");
  const [res, setRes] = useState<AccessCheck | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(""); setRes(null);
    try { setRes(await api.admin.accessCheckUser(login.trim())); } catch (x) { setErr((x as ApiError).message); }
    setBusy(false);
  };
  return (
    <div className="card form">
      <h3 style={{ margin: 0 }}>Проверить пользователя</h3>
      <p className="help" style={{ margin: 0 }}>Покажет, пропустят ли пользователя сохранённые правила, по какой группе и получит ли он права администратора. Пароль не требуется; проверяются сохранённые, а не только что изменённые группы.</p>
      <form className="row" onSubmit={(e) => void run(e)}>
        <input value={login} onChange={(e) => setLogin(e.target.value)} placeholder="Логин: user1 или user1@example.local" aria-label="Логин пользователя" spellCheck={false} />
        <button className="btn" disabled={busy || !login.trim()}>{busy ? "Проверка…" : "Проверить"}</button>
      </form>
      {err && <div className="alert error" role="status">{err}</div>}
      {res && !res.found && <div className="alert info" role="status">{res.message}</div>}
      {res && res.found && (
        <div className={`alert ${res.would_log_in ? "ok" : "error"}`} role="status">
          <b>{res.display_name}</b> ({res.login}{res.source ? `, ${res.source}` : ""}) — {res.would_log_in ? "войти сможет" : "войти не сможет"}
          {!res.would_log_in && res.reason && <>: {REASONS[res.reason] ?? res.reason}</>}.
          <div className="small">
            {res.restricted ? "Список групп допуска включён" : "Список групп допуска пуст — ограничения нет"}
            {res.allowed_via && res.allowed_via.length > 0 && <> · допущен по группам: {res.allowed_via.map(short).join(", ")}</>}
            {" · "}{res.admin ? <>права администратора: да ({(res.admin_via ?? []).map(short).join(", ") || "по группам"})</> : "права администратора: нет"}
            {" · "}групп в каталоге (с вложенными): {res.groups_total ?? 0}
          </div>
        </div>
      )}
    </div>
  );
}

/** «Доступ к системе»: кто вообще может войти (отдельно от прав администратора). Пусто — входит любой активный пользователь каталога. */
export default function LoginAccessAdmin({ onOpen }: { onOpen?: (id: string) => void }) {
  const [groups, setGroups] = useState<string[]>([]);
  const [st, setSt] = useState<AccessStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    void api.admin.settings("access").then((v) => { setGroups((v.user_groups as unknown as string[]) ?? []); setLoaded(true); }).catch((e) => setMsg({ ok: false, text: (e as ApiError).message }));
    void api.admin.accessStatus().then(setSt).catch(() => undefined);
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.admin.saveSettings("access", { user_groups: groups } as never);
      setMsg({ ok: true, text: groups.length ? "Сохранено. Правило действует со следующей попытки входа, перезапуск не нужен; уже открытые сеансы не прерываются." : "Сохранено. Ограничения входа нет." });
      load();
    } catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    setBusy(false);
  };

  return (
    <section>
      <div className="row"><h2>Доступ к системе</h2></div>
      <p className="muted">Кто из пользователей каталога вообще может войти. Проверка идёт после успешной проверки пароля: если пользователь не входит ни в одну из разрешённых групп, вход отклоняется — сеанс не создаётся, список переговорок и история недоступны. Это отдельно от прав администратора ({onOpen ? <a href="#" onClick={(e) => { e.preventDefault(); onOpen("access"); }}>«Доступ к администрированию»</a> : "«Доступ к администрированию»"}). Гостевые ссылки и локальный администратор от этого списка не зависят.</p>
      {st && !st.restricted && (
        <div className="alert error" role="status"><b>Ограничение входа не настроено.</b> Сейчас в систему может войти любой активный пользователь каталога. Добавьте разрешённые группы — после добавления первой группы список станет закрытым (войдут только члены групп и администраторы).</div>
      )}
      {st?.env_user_group && <div className="alert info small">Одна группа допуска задана в файле установки (<code>LDAP_ACCESS_GROUP_DN</code>); она действует вместе с перечисленными ниже.</div>}
      {!loaded ? <div className="muted">Загрузка…</div> : (
        <div className="card form">
          <GroupList title="Разрешённые группы" value={groups} onChange={setGroups}
                     help="Достаточно членства в одной из групп (вложенные группы учитываются). Администраторы из групп администраторов входят всегда."
                     empty="Групп нет — вход разрешён любому активному пользователю каталога." />
          {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
          <div className="row form-actions"><button className="btn primary" onClick={() => void save()} disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</button></div>
        </div>
      )}
      <CheckUser />
    </section>
  );
}
