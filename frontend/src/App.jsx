import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, AlertTriangle, Info, RefreshCw } from 'lucide-react';
import { API_BASE, api, isGo } from './api';
import useLiveFeed from './hooks/useLiveFeed';
import Header from './components/Header';
import ScenarioPanel from './components/ScenarioPanel';
import MetricsPanel from './components/MetricsPanel';
import AgentCard from './components/AgentCard';
import LiveFeed from './components/LiveFeed';
import AuditLog from './components/AuditLog';
import PolicyEditorModal from './components/PolicyEditorModal';
import PolicyHistoryModal from './components/PolicyHistoryModal';
import CreateAgentModal from './components/CreateAgentModal';
import { SectionTitle } from './components/ui';

const PAGE = 50;

const matchesFilters = (entry, filters) =>
  (filters.agent === 'all' || entry.agent_id === filters.agent) &&
  (filters.outcome === 'all' || entry.outcome === filters.outcome);

export default function App() {
  const [agents, setAgents] = useState([]);
  const [liveFeed, setLiveFeed] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [auditLimit, setAuditLimit] = useState(PAGE);
  const [filters, setFilters] = useState({ agent: 'all', outcome: 'all' });
  const [metrics, setMetrics] = useState(null);
  const [simulatorRunning, setSimulatorRunning] = useState(true);
  const [verification, setVerification] = useState(null);
  const [verifying, setVerifying] = useState(false);
  const [editing, setEditing] = useState(null);
  const [historyFor, setHistoryFor] = useState(null);
  const [creating, setCreating] = useState(false);
  const [feedHovered, setFeedHovered] = useState(false);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);

  const feedHoveredRef = useRef(false);
  feedHoveredRef.current = feedHovered;
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  const flash = (text, tone = 'info') => {
    setNotice({ text, tone });
    setTimeout(() => setNotice(null), 4000);
  };

  // Wraps an operator call: surfaces backend errors instead of failing silently.
  const run = async (fn, success) => {
    try {
      const result = await fn();
      if (success) flash(typeof success === 'function' ? success(result) : success);
      return result;
    } catch (e) {
      flash(e.message, 'error');
      return null;
    }
  };

  const fetchAgents = useCallback(async () => {
    try {
      setAgents(await api('/agents'));
      setIsError(false);
    } catch {
      setIsError(true);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const fetchAuditLogs = useCallback(async (limit) => {
    const params = new URLSearchParams({ limit });
    if (filters.agent !== 'all') params.set('agent_id', filters.agent);
    if (filters.outcome !== 'all') params.set('outcome', filters.outcome);
    try {
      setAuditLogs(await api(`/audit-log?${params}`));
    } catch (e) {
      console.error(e);
    }
  }, [filters]);

  const fetchMetrics = useCallback(async () => {
    try {
      setMetrics(await api('/metrics'));
    } catch {
      /* backend offline banner already covers this */
    }
  }, []);

  useEffect(() => {
    fetchAgents();
    api('/simulator').then((s) => setSimulatorRunning(s.simulator_running)).catch(() => {});
  }, [fetchAgents]);

  useEffect(() => {
    setAuditLimit(PAGE);
    fetchAuditLogs(PAGE);
  }, [fetchAuditLogs]);

  useEffect(() => {
    fetchMetrics();
    const id = setInterval(fetchMetrics, 2000);
    return () => clearInterval(id);
  }, [fetchMetrics]);

  const wsConnected = useLiveFeed((entry) => {
    if (entry._agents) {
      setAgents((prev) => {
        const byId = new Map(prev.map((a) => [a.agent_id, a]));
        entry._agents.forEach((a) => byId.set(a.agent_id, a));
        return [...byId.values()];
      });
    }
    if (!feedHoveredRef.current) {
      setLiveFeed((prev) => (prev.some((e) => e.action_id === entry.action_id) ? prev : [entry, ...prev.slice(0, 49)]));
    }
    if (matchesFilters(entry, filtersRef.current)) {
      setAuditLogs((prev) => (prev.some((e) => e.action_id === entry.action_id) ? prev : [entry, ...prev]));
    }
  });

  const attempt = (agent_id, action_type, amount) =>
    api('/simulate/action', { method: 'POST', body: { agent_id, action_type, amount } });

  const describe = (entry) =>
    entry.outcome === 'require_approval'
      ? `HELD FOR APPROVAL · ${entry.agent_id} · ${entry.action_type} $${entry.amount}`
      : isGo(entry.outcome)
      ? `ALLOWED · ${entry.agent_id} · ${entry.action_type} $${entry.amount}`
      : `BLOCKED · ${entry.agent_id} · ${entry.action_type} → ${entry.rule_triggered}`;

  const handleFleetStop = async () => {
    if (!window.confirm('Stop ALL agents in the fleet immediately?')) return;
    await run(() => api('/emergency-stop', { method: 'POST' }), 'Fleet emergency stop engaged — all agent actions are now refused');
    fetchMetrics();
  };

  const handleRevoke = async (agentId) => {
    const reason = window.prompt(`Revoke ${agentId}? Revocation survives "Resume Fleet" until explicitly reinstated.\n\nReason:`, 'Suspected compromise');
    if (reason === null) return;
    await run(() => api(`/agents/${agentId}/revoke`, { method: 'POST', body: { reason } }), `${agentId} revoked`);
  };

  const triggerScenario = async (id) => {
    setBusy(true);
    try {
      if (id === 'approved') {
        await run(() => attempt('travel_agent_01', 'flight_rebook', 450), describe);
      } else if (id === 'unpermitted') {
        await run(() => attempt('travel_agent_01', 'credit_limit_increase', 1000), describe);
      } else if (id === 'spend_cap') {
        for (let i = 0; i < 5; i++) {
          const entry = await run(() => attempt('travel_agent_01', 'flight_rebook', 550));
          if (!entry || entry.outcome === 'block') {
            if (entry) flash(describe(entry));
            break;
          }
        }
      } else if (id === 'revoke') {
        await run(() => api('/agents/credit_limit_03/revoke', { method: 'POST', body: { reason: 'Anomalous behaviour detected (demo)' } }));
        await run(() => attempt('credit_limit_03', 'credit_limit_increase', 1500), describe);
      } else if (id === 'fleet_stop') {
        await handleFleetStop();
      }
    } finally {
      setBusy(false);
    }
  };

  const handleVerify = async () => {
    setVerifying(true);
    try {
      setVerification(await api('/audit-log/verify'));
    } catch {
      setVerification({ valid: false, reason: 'request failed' });
    } finally {
      setVerifying(false);
    }
  };

  const cardHandlers = {
    onEdit: setEditing,
    onHistory: setHistoryFor,
    onResetSpend: (id) => run(() => api(`/agents/${id}/reset-spend`, { method: 'POST' }), `${id} spend reset`),
    onStop: (id) => run(() => api(`/emergency-stop/${id}`, { method: 'POST' }), `${id} stopped`),
    onResume: (id) => run(() => api(`/resume/${id}`, { method: 'POST' }), `${id} resumed`),
    onRevoke: handleRevoke,
    onReinstate: (id) => run(() => api(`/agents/${id}/reinstate`, { method: 'POST' }), `${id} reinstated`),
  };

  const fleetStopped = metrics?.fleet?.fleet_stopped;

  return (
    <div className="min-h-screen bg-[#0b0f19] text-gray-100 font-sans p-4 md:p-6">
      <Header
        wsConnected={wsConnected}
        simulatorRunning={simulatorRunning}
        onToggleSimulator={() =>
          run(() => api(`/simulator/toggle?running=${!simulatorRunning}`, { method: 'POST' })).then(
            (r) => r && setSimulatorRunning(r.simulator_running)
          )
        }
        onResumeFleet={() => run(() => api('/resume-all', { method: 'POST' }), 'Fleet resumed (revoked agents stay revoked)').then(fetchMetrics)}
        onResetAllSpend={() => run(() => api('/reset-all-spend', { method: 'POST' }), 'All agent spend reset')}
        onFleetStop={handleFleetStop}
        onRegister={() => setCreating(true)}
      />

      {isError && (
        <div className="mb-4 px-4 py-3 rounded-xl bg-[#C1443B]/10 border border-[#C1443B]/40 text-[#C1443B] text-xs font-mono flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          Backend offline — cannot reach {API_BASE}. Start the FastAPI server and refresh.
        </div>
      )}

      {fleetStopped && (
        <div className="mb-4 px-4 py-3 rounded-xl bg-[#C1443B] text-white text-xs font-mono font-bold flex items-center gap-2 animate-pulse">
          <AlertTriangle className="w-4 h-4" /> FLEET EMERGENCY STOP ACTIVE — every agent action is being refused. Use “Resume Fleet” to lift it.
        </div>
      )}

      {notice && (
        <div
          role="status"
          className={`fixed bottom-6 right-6 z-40 max-w-md px-4 py-3 rounded-xl border text-xs font-mono shadow-2xl flex items-center gap-2 ${
            notice.tone === 'error' ? 'bg-[#1F1314] border-[#C1443B]/60 text-[#C1443B]' : 'bg-[#14171B] border-[#E8A33D]/50 text-[#E6E8EB]'
          }`}
        >
          {notice.tone === 'error' ? <AlertTriangle className="w-4 h-4" /> : <Info className="w-4 h-4 text-[#E8A33D]" />}
          {notice.text}
        </div>
      )}

      <ScenarioPanel onTrigger={triggerScenario} busy={busy} />

      <MetricsPanel metrics={metrics} />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <div id="fleet" className="xl:col-span-2">
          <SectionTitle
            icon={Activity}
            right={
              <button onClick={fetchAgents} className="text-[#8A93A3] hover:text-[#E6E8EB] text-xs font-mono flex items-center gap-1">
                <RefreshCw className="w-3 h-3" /> Refresh
              </button>
            }
          >
            Fleet Agent Status ({agents.length})
          </SectionTitle>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {isLoading
              ? Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="panel-base p-4 animate-pulse space-y-3">
                    <div className="h-4 bg-[#1C2128] rounded w-3/4" />
                    <div className="h-3 bg-[#1C2128] rounded w-1/2" />
                    <div className="h-2 bg-[#1C2128] rounded w-full mt-4" />
                  </div>
                ))
              : agents.map((agent) => <AgentCard key={agent.agent_id} agent={agent} {...cardHandlers} />)}
          </div>
        </div>

        <LiveFeed entries={liveFeed} paused={feedHovered} onHoverChange={setFeedHovered} />
      </div>

      <AuditLog
        logs={auditLogs}
        agents={agents}
        filters={filters}
        onFilterChange={setFilters}
        onLoadMore={() => {
          const next = auditLimit + PAGE;
          setAuditLimit(next);
          fetchAuditLogs(next);
        }}
        onVerify={handleVerify}
        verifying={verifying}
        verification={verification}
      />

      {editing && (
        <PolicyEditorModal
          agent={editing}
          onClose={() => setEditing(null)}
          onSave={async (agentId, policy) => {
            const res = await run(() => api(`/agents/${agentId}/policy`, { method: 'PATCH', body: policy }), (r) => `${agentId} → policy v${r.policy_version} published`);
            if (res) setEditing(null);
          }}
        />
      )}

      {historyFor && (
        <PolicyHistoryModal
          agent={agents.find((a) => a.agent_id === historyFor.agent_id) || historyFor}
          onClose={() => setHistoryFor(null)}
          onRollback={(agentId, version) =>
            run(() => api(`/agents/${agentId}/policy/rollback/${version}`, { method: 'POST' }), (r) => `Rolled back to v${version} (now v${r.policy_version})`)
          }
        />
      )}

      {creating && (
        <CreateAgentModal
          onClose={() => setCreating(false)}
          onCreate={async (payload) => {
            const res = await run(() => api('/agents', { method: 'POST', body: payload }), `${payload.agent_id} registered`);
            if (res) setCreating(false);
          }}
        />
      )}
    </div>
  );
}
