"use client";

import {
  ArrowDownToLine, ArrowRight, ArrowUpRight, BookOpen, ChevronLeft, ChevronRight, CircleHelp,
  Database, FileText, Layers3, LoaderCircle, LogOut, Moon, Search, Sparkles, Sun, Trash2,
  UploadCloud, X,
} from "lucide-react";
import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Answer, type Citation, type DocumentList, type DocumentView, fileBlob, type Question, type WorkspaceConfig } from "./api";

const example = "What is the cancellation notice period?";
const keyName = "doculens.workspace.v1";
const getError = (error: unknown) => error instanceof Error ? error.message : "Something went wrong. Try again.";
type Exchange = { question: string; mode: "answer" | "search"; result: Answer };
type Source = { id: string; filename: string; page: number; excerpt?: string };
type Report = { metadata: { provider: string; dataset_version: string; timestamp: string }; metrics: Record<string, number | null>; counts: { questions: number } };

export default function WorkspacePage() {
  const [token, setToken] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [accessKey, setAccessKey] = useState("");
  const [config, setConfig] = useState<WorkspaceConfig>();
  const [documents, setDocuments] = useState<DocumentView[]>([]);
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState("all");
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState("");
  const [mode, setMode] = useState<"answer" | "search">("answer");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [source, setSource] = useState<Source>();
  const [image, setImage] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState("");
  const [fetching, setFetching] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [dark, setDark] = useState(false);
  const [report, setReport] = useState<Report>();
  const [reportError, setReportError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const questionInput = useRef<HTMLTextAreaElement>(null);
  const sourceDialog = useRef<HTMLDialogElement>(null);
  const deleteDialog = useRef<HTMLDialogElement>(null);
  const reportDialog = useRef<HTMLDialogElement>(null);
  const pendingDelete = useRef<DocumentView>(null);
  const pendingName = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    // Read browser storage only after hydration.
    const timer = window.setTimeout(() => { setToken(localStorage.getItem(keyName) ?? ""); setLoaded(true); }, 0);
    void api<WorkspaceConfig>("/workspace-config").then(setConfig).catch((e: unknown) => setError(getError(e)));
    return () => window.clearTimeout(timer);
  }, []);

  const disconnect = useCallback((message = "") => {
    localStorage.removeItem(keyName);
    setToken(""); setDocuments([]); setHistory([]); setScope(""); setError(message);
  }, []);

  const refresh = useCallback(async (credential: string, signal?: AbortSignal) => {
    const all: DocumentView[] = [];
    let cursor: string | null | undefined;
    do {
      const page: DocumentList = await api("/documents?limit=100" + (cursor ? `&cursor=${cursor}` : ""), credential, { signal });
      all.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    if (!signal?.aborted) setDocuments(all);
  }, []);

  const indexing = documents.some((doc) => ["queued", "processing"].includes(doc.status));
  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await refresh(token, controller.signal); }
      catch (e) {
        if (controller.signal.aborted) return;
        if (e instanceof ApiError && e.status === 401) { disconnect(getError(e)); return; }
        setError(getError(e));
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, indexing ? 1250 : 7000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [token, indexing, refresh, disconnect]);

  useEffect(() => {
    if (!source || !token) return;
    const controller = new AbortController();
    let url = "";
    if (!sourceDialog.current?.open) sourceDialog.current?.showModal();
    void fileBlob(`/documents/${source.id}/pages/${source.page}`, token, controller.signal).then((blob) => {
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(blob);
      setImage(url);
    }).catch((e: unknown) => { if (!controller.signal.aborted) setPreviewError(getError(e)); });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [source, token]);

  async function connect(event: FormEvent) {
    event.preventDefault();
    setWorking("connect"); setError("");
    try {
      const session = await api<{ token: string }>("/workspaces", undefined, { method: "POST", body: JSON.stringify({ access_key: accessKey }) });
      localStorage.setItem(keyName, session.token);
      setToken(session.token); setAccessKey("");
    } catch (e) { setError(getError(e)); }
    finally { setWorking(""); }
  }

  async function mutate(operation: string, path: string, method = "POST", body?: FormData) {
    setWorking(operation); setError("");
    try {
      await api(path, token, { method, body });
      await refresh(token);
    } catch (e) { setError(getError(e)); }
    finally { setWorking(""); }
  }

  function upload(file?: File) {
    if (!file || working) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) { setError("Choose a PDF file."); return; }
    if (config && file.size > config.max_upload_bytes) { setError("This PDF exceeds the upload size limit."); return; }
    const body = new FormData();
    body.append("file", file, file.name);
    void mutate("upload", "/documents", "POST", body);
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || fetching) return;
    const text = question.trim();
    const body: Question = { question: text, document_ids: activeScope ? [activeScope] : null, include_evidence: true, mode };
    setFetching(true); setError("");
    try {
      const result = await api<Answer>("/questions", token, { method: "POST", body: JSON.stringify(body) });
      setHistory((old) => [{ question: text, mode, result }, ...old]);
      setQuestion("");
    } catch (e) { setError(getError(e)); }
    finally { setFetching(false); }
  }

  async function showReport() {
    reportDialog.current?.showModal();
    setReportError("");
    try { setReport(await api<Report>("/evaluation/report")); }
    catch (e) { setReportError(getError(e)); }
  }

  async function download() {
    if (!source) return;
    try {
      const blob = await fileBlob(`/documents/${source.id}/file`, token);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = source.filename; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 3000);
    } catch (e) { setPreviewError(getError(e)); }
  }

  function askDelete(doc: DocumentView) {
    pendingDelete.current = doc;
    if (pendingName.current) pendingName.current.textContent = doc.filename;
    deleteDialog.current?.showModal();
  }

  const indexed = documents.filter((d) => d.searchable);
  const activeScope = indexed.some((d) => d.id === scope) ? scope : "";
  const shown = documents.filter((d) => d.filename.toLowerCase().includes(filter.toLowerCase()) && (status === "all" || status === d.status));
  const openSource = (value: Source) => { setImage(""); setPreviewError(""); setSource(value); };
  const openCitation = (c: Citation) => openSource({ id: c.document_id, filename: c.filename, page: c.page, excerpt: c.excerpt });
  const totalPages = documents.find((d) => d.id === source?.id)?.page_count ?? 1;
  const busy = !!working || fetching;

  return <div className={dark ? "shell dark" : "shell"}>
    <header className="top"><div className="frame top-inner">
      <Link href="/" className="brand"><span className="brand-icon"><Layers3 size={21} /></span>DocuLens<span>AI</span></Link>
      <div className="top-actions">
        <button className="quiet" onClick={() => void showReport()}><Database size={15} /> Evaluation</button>
        <button className="icon" aria-label={dark ? "Use light theme" : "Use dark theme"} onClick={() => setDark(!dark)}>{dark ? <Sun size={17} /> : <Moon size={17} />}</button>
        {token && <button className="icon" aria-label="Disconnect workspace" disabled={busy} onClick={() => disconnect()}><LogOut size={17} /></button>}
      </div>
    </div></header>
    <main className="frame">
      <section className="hero">
        <div className="eyebrow"><span /> EVIDENCE WORKSPACE</div>
        <h1>Answers you can verify<span>.</span></h1>
        <p>Search your PDFs, ask a question, and trace every citation back to the original page.</p>
        <div className="demo-note"><CircleHelp size={15} /> Independent demonstration project using synthetic or permitted sample documents</div>
      </section>
      {error && <div className="alert" role="alert">{error}<button className="icon" onClick={() => setError("")} aria-label="Dismiss error"><X size={16} /></button></div>}
      {!loaded ? <p role="status">Loading workspace…</p> : !token ? <section className="card welcome">
        <div className="feature-icon"><Layers3 size={25}/></div><h2>Open a private workspace</h2>
        <p>Enter the access key configured for your local demo. Each new workspace has its own document library.</p>
        <form onSubmit={connect}><label htmlFor="access">Workspace access key</label><input id="access" type="password" autoComplete="off" value={accessKey} onChange={(e) => setAccessKey(e.target.value)} required />
          <button className="primary" disabled={working === "connect"}>Connect workspace <ArrowRight size={16} /></button>
        </form>
      </section> : <div className="workspace">
        <section className="card library" aria-labelledby="library">
          <div className="section-label"><BookOpen size={19}/><h2 id="library">Document library</h2><span className="count">{documents.length}</span></div>
          <p className="muted">Upload and manage your source material.</p>
          <button className={`drop ${dragging ? "dragging" : ""}`} disabled={busy} onClick={() => fileInput.current?.click()}
            onDragOver={(e) => { e.preventDefault(); if (!busy) setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); if (!busy) upload(e.dataTransfer.files[0]); }}>
            {working === "upload" ? <LoaderCircle className="spin" size={23}/> : <UploadCloud size={25}/>}
            <strong>{working === "upload" ? "Uploading…" : "Drop a PDF or browse files"}</strong>
            <small>{config ? `Up to ${(config.max_upload_bytes / 1024**2).toFixed(1).replace(/\.0$/, "")} MB · ${config.max_pdf_pages} pages` : "Text-readable PDF"}</small>
          </button>
          <input ref={fileInput} aria-label="Upload a PDF" className="hidden" type="file" accept=".pdf,application/pdf" onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
          <button className="sample" disabled={busy} onClick={() => void mutate("samples", "/samples")}><Layers3 size={16}/>{working === "samples" ? "Loading samples…" : "Load synthetic samples"}<ArrowUpRight size={14}/></button>
          {documents.length > 0 && <div className="filters">
            <input aria-label="Filter by filename" placeholder="Find a PDF…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="all">All states</option><option value="indexed">Indexed</option><option value="queued">Queued</option><option value="processing">Processing</option><option value="failed">Failed</option>
            </select>
          </div>}
          <ul className="docs" aria-live="polite">{shown.map((d) => <li key={d.id}>
            <span className="doc-icon"><FileText size={18}/></span><div className="doc-meta"><strong title={d.filename}>{d.filename}</strong><small><span className={`state ${d.status}`}>{d.status}</span>{d.page_count ? ` · ${d.page_count} pages` : ""}</small>
              {d.error_message && <small>{d.error_message}</small>}
              {d.status === "indexed" && !d.searchable && <small>Delete and re-upload to index for the active provider.</small>}
              {d.retryable && <button className="link" disabled={busy} onClick={() => void mutate("retry", `/documents/${d.id}/retry`)}>Retry indexing</button>}
            </div>
            {d.status === "indexed" && <button className="icon" aria-label={`Preview ${d.filename}`} onClick={() => openSource({ id: d.id, filename: d.filename, page: 1 })}><ArrowUpRight size={17}/></button>}
            <button className="icon danger" disabled={busy} aria-label={`Delete ${d.filename}`} onClick={() => askDelete(d)}><Trash2 size={16}/></button>
          </li>)}</ul>
          {shown.length === 0 && <p className="empty-docs">{documents.length ? "No documents match your filters." : "Your library is empty. Upload a PDF or load samples to start."}</p>}
        </section>
        <section className="card research" aria-labelledby="research">
          <div className="section-label"><Sparkles size={20}/><h2 id="research">Ask your documents</h2>
            <span className="profile">{config?.provider === "openai" ? "OpenAI" : "Local · extractive"}</span></div>
          <p className="muted">Answers and matching passages are linked to their sources.</p>
          <form onSubmit={ask} className="question-form">
            <label className="sr-only" htmlFor="question">Question</label>
            <textarea ref={questionInput} id="question" placeholder="Ask a question about your documents…" rows={3} maxLength={1000} value={question} onChange={(e) => setQuestion(e.target.value)} />
            <div className="controls">
              <select aria-label="Document scope" value={activeScope} onChange={(e) => setScope(e.target.value)}>
                <option value="">All searchable PDFs</option>{indexed.map((d) => <option key={d.id} value={d.id}>{d.filename}</option>)}
              </select>
              <select aria-label="Response mode" value={mode} onChange={(e) => setMode(e.target.value as "answer" | "search")}>
                <option value="answer">Ask for answer</option><option value="search">Search passages</option>
              </select>
              <button className="primary" disabled={!indexed.length || !question.trim() || busy}>{fetching ? <LoaderCircle className="spin" size={17}/> : mode === "answer" ? <Sparkles size={16}/> : <Search size={16}/>} {mode === "answer" ? "Ask" : "Search"}</button>
            </div>
          </form>
          {!indexed.length && <p className="hint" role="status">{indexing ? "Indexing documents. Questions will be available shortly." : "Add a searchable PDF to ask a question."}</p>}
          <div aria-live="polite">{fetching && <p className="hint" role="status">Looking for supporting passages…</p>}
            {!history.length && <div className="empty-answer"><div className="feature-icon"><Search size={23}/></div><h3>Start with a question</h3><p>With the sample library, try:</p><button className="suggestion" onClick={() => { setQuestion(example); questionInput.current?.focus(); }}>{example}<ArrowRight size={15}/></button></div>}
            {history.map(({ question, mode, result }) => <article className="answer" key={result.request_id}>
              <div className="asked"><span>QUESTION</span><h3>{question}</h3></div>
              <div className="answer-label"><Sparkles size={16}/>{mode === "search" ? "Search results" : result.abstained ? "Insufficient evidence" : "Answer"}</div>
              {result.answer && <p className="answer-text">{result.answer}</p>}
              {mode === "search" && <p className="muted">{result.retrieved_count ? "Ranked by similarity; matches may be incomplete." : "No matching passages were found."}</p>}
              {result.citations.length > 0 && <div className="chips">{result.citations.map((c, i) => <button key={c.chunk_id} className="chip" onClick={() => openCitation(c)}><span>{i + 1}</span>{c.filename} · p. {c.page}<ArrowUpRight size={13}/></button>)}</div>}
              {result.evidence.length > 0 && <details open={mode === "search" ? true : undefined}><summary>Retrieved passages ({result.evidence.length})</summary>{result.evidence.map((c) => <div className="passage" key={c.chunk_id}><button className="link" onClick={() => openCitation(c)}>{c.filename} · page {c.page} <ArrowUpRight size={14}/></button><p>{c.excerpt}</p></div>)}</details>}
            </article>)}
          </div>
        </section>
      </div>}
      <footer><span>DocuLens AI / Independent demo</span><span>Review original sources before relying on answers.</span></footer>
    </main>
    <dialog ref={sourceDialog} className="source-dialog" aria-labelledby="source-title" onClose={() => setSource(undefined)}>
      {source && <>
        <div className="dialog-head"><div><div className="eyebrow">SOURCE PREVIEW</div><h2 id="source-title">{source.filename}</h2></div><button className="icon" aria-label="Close preview" onClick={() => sourceDialog.current?.close()}><X size={22}/></button></div>
        <div className="toolbar">
          <button className="icon" aria-label="Previous page" disabled={source.page <= 1} onClick={() => openSource({...source, page: source.page - 1, excerpt: undefined})}><ChevronLeft size={18}/></button>
          <span>Page {source.page} of {totalPages}</span>
          <button className="icon" aria-label="Next page" disabled={source.page >= totalPages} onClick={() => openSource({...source, page: source.page + 1, excerpt: undefined})}><ChevronRight size={18}/></button>
          <button className="quiet download" onClick={() => void download()}><ArrowDownToLine size={16}/> Download PDF</button>
        </div>
        {source.excerpt && <p className="quote">{source.excerpt}</p>}
        {previewError && <p role="alert" className="alert">{previewError}</p>}
        {image ? /* eslint-disable-next-line @next/next/no-img-element */
          <img className="page-preview" src={image} alt={`Rendered page ${source.page} of ${source.filename}`}/> : !previewError && <p role="status">Rendering page…</p>}
      </>}
    </dialog>
    <dialog ref={deleteDialog} aria-labelledby="delete-title"><div className="dialog-head"><h2 id="delete-title">Delete document?</h2></div>
      <p><span ref={pendingName}/> will be removed from search immediately.</p>
      <div className="dialog-actions"><button className="quiet" onClick={() => deleteDialog.current?.close()}>Cancel</button><button className="primary" onClick={() => {
        const doc = pendingDelete.current;
        deleteDialog.current?.close();
        if (doc) void mutate("delete", `/documents/${doc.id}`, "DELETE");
      }}>Delete PDF</button></div>
    </dialog>
    <dialog ref={reportDialog} aria-labelledby="report-title"><div className="dialog-head"><h2 id="report-title">Evaluation report</h2><button className="icon" aria-label="Close evaluation" onClick={() => reportDialog.current?.close()}><X size={20}/></button></div>
      {reportError ? <p role="alert">{reportError}</p> : report ? <>
        <p>{report.counts.questions} synthetic questions · {report.metadata.provider} · {report.metadata.dataset_version}</p>
        <dl className="metrics">{Object.entries(report.metrics).map(([name, value]) => <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{value === null ? "—" : name.endsWith("_ms") ? `${value.toFixed(1)} ms` : `${(value * 100).toFixed(1)}%`}</dd></div>)}</dl>
        <p className="hint">Measured {new Date(report.metadata.timestamp).toLocaleString()}. Synthetic results do not establish real-world accuracy.</p>
      </> : <p role="status">Loading measurements…</p>}
    </dialog>
  </div>;
}
