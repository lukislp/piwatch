import { useEffect, useState } from "react";
import { Events, Healthchecks, Logs, Nodes, Overview, Workloads } from "./pages";
import { authRequired, clearToken, getToken, login, useClusterStore } from "./store";
import { STATUS, useTheme } from "./theme";
import { Dot } from "./components";

const TABS = ["Overview", "Nodes", "Workloads", "Checks", "Logs", "Events"] as const;

function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    if (await login(pw)) onDone();
    else setErr("Invalid password");
  };
  return (
    <form className="login" onSubmit={submit}>
      <h1>📡 PiWatch</h1>
      <input type="password" placeholder="Password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
      {err && <div className="err">{err}</div>}
      <button className="btn" type="submit">Log in</button>
    </form>
  );
}

export default function App() {
  const [mode, toggleTheme] = useTheme();
  const [needsLogin, setNeedsLogin] = useState<boolean | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Overview");
  const { snapshot, status, reconnect } = useClusterStore();

  useEffect(() => {
    authRequired().then((req) => setNeedsLogin(req && !getToken()));
  }, []);

  useEffect(() => {
    if (status === "unauthorized") {
      clearToken();
      setNeedsLogin(true);
    }
  }, [status]);

  if (needsLogin === null) return null;
  if (needsLogin) return <Login onDone={() => { setNeedsLogin(false); reconnect(); }} />;

  const connColor = status === "open" ? STATUS.good : status === "connecting" ? STATUS.warning : STATUS.critical;
  const connText = status === "open" ? "connected" : status === "connecting" ? "connecting …" : "disconnected – reconnecting …";

  return (
    <>
      <header className="top">
        <h1>📡 PiWatch</h1>
        {snapshot?.demo_mode && <span className="badge">DEMO</span>}
        <span className="badge conn"><Dot color={connColor} />{connText}</span>
        <div className="spacer" />
        <button className="ghost" onClick={toggleTheme} title="Toggle theme">{mode === "dark" ? "☀️ Light" : "🌙 Dark"}</button>
        <button className="ghost" onClick={() => { clearToken(); location.reload(); }}>Log out</button>
      </header>
      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </nav>
      {!snapshot ? (
        <div className="card muted">Waiting for data …</div>
      ) : (
        <>
          {tab === "Overview" && <Overview snap={snapshot} mode={mode} />}
          {tab === "Nodes" && <Nodes snap={snapshot} mode={mode} />}
          {tab === "Workloads" && <Workloads snap={snapshot} mode={mode} />}
          {tab === "Checks" && <Healthchecks snap={snapshot} />}
          {tab === "Logs" && <Logs snap={snapshot} />}
          {tab === "Events" && <Events snap={snapshot} />}
        </>
      )}
    </>
  );
}
