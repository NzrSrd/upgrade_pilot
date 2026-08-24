import { useEffect, useState } from "react";
import { ShieldAlert, CheckCircle } from "lucide-react";

type Health = { status: string; version: string };

// Only "ok" earns the green tick. The backend previously returned a
// hardcoded "ok" and this component rendered a green tick for *any* successful
// response, so a degraded backend was reported to the user as healthy twice
// over. The status word was already displayed; the icon was the part that lied.
const isHealthy = (health: Health) => health.status === "ok";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main className="min-h-screen bg-surface p-8 font-sans">
      <h1 className="text-2xl font-semibold">UpgradePilot</h1>
      <p className="mt-1 text-sm opacity-70">Dependency upgrade risk agent</p>

      <div className="mt-6 flex items-center gap-2 rounded-lg bg-surface-sunken p-4">
        {error ? (
          <>
            <ShieldAlert className="size-5 text-risk-high" />
            <span>Backend unreachable: {error}</span>
          </>
        ) : health ? (
          <>
            {isHealthy(health) ? (
              <CheckCircle className="size-5 text-risk-low" />
            ) : (
              <ShieldAlert className="size-5 text-risk-medium" />
            )}
            <span>
              Backend {health.status} · v{health.version}
            </span>
          </>
        ) : (
          <span className="opacity-60">Checking backend…</span>
        )}
      </div>
    </main>
  );
}
