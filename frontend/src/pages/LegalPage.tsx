import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ApiError, type LegalPublic } from "../api";
import { Markdown } from "../components/Markdown";
import { useSite } from "../site";
import { fmt } from "../util";

/** Документ организации (политика, согласие, соглашение, правила): отдельная страница внутри системы, открывается без входа. Markdown показывается безопасным рендерером (без HTML). */
export default function LegalPage() {
  const { kind = "" } = useParams();
  const site = useSite();
  const [doc, setDoc] = useState<LegalPublic | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setDoc(null); setErr("");
    api.legal.doc(kind).then(setDoc).catch((e) => setErr((e as ApiError).status === 404 ? "Документ не опубликован." : (e as ApiError).message));
  }, [kind]);
  useEffect(() => { if (doc) document.title = `${doc.title} · ${site.name}`; }, [doc, site.name]);
  return (
    <main className="legal-page">
      <article className="card legal-card">
        <p><Link to="/">← {site.name}</Link></p>
        {err && <div className="alert info" role="status">{err}</div>}
        {doc && (
          <>
            <h1>{doc.title}</h1>
            <p className="muted small">Редакция {doc.version}{doc.published_at ? ` от ${fmt(doc.published_at)}` : ""}</p>
            <Markdown source={doc.content_md} />
          </>
        )}
        {!doc && !err && <p className="muted">Загрузка…</p>}
      </article>
    </main>
  );
}
