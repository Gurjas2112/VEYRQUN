import { Pause, Play, Plus, RefreshCw, RotateCcw, ShieldAlert, ShieldHalf } from 'lucide-react';
import { Button } from './ui';

export default function Header({ wsConnected, simulatorRunning, onToggleSimulator, onResumeFleet, onResetAllSpend, onFleetStop, onRegister }) {
  return (
    <header className="panel-base p-4 mb-6 flex flex-col lg:flex-row items-center justify-between gap-4">
      <div className="flex items-center space-x-3">
        <div className="p-2.5 bg-[#1C2128] rounded-xl text-[#E8A33D]">
          <ShieldHalf className="w-6 h-6" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight text-[#E6E8EB]">
            <span className="tracking-[0.2em]">VEYRQUN</span>
            <span className="text-[#8A93A3] font-normal"> | Agent Governance Control Tower</span>
          </h1>
          <p className="text-xs text-[#8A93A3] flex items-center gap-2 mt-0.5 font-mono">
            <span>Governance Layer for Financial Agents</span>
            <span>•</span>
            <span className={`inline-block w-2 h-2 rounded-full ${wsConnected ? 'bg-[#4C9A6A]' : 'bg-[#C1443B]'}`} />
            <span>{wsConnected ? 'Live Feed Online' : 'Disconnected'}</span>
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2.5 text-xs font-medium">
        <Button onClick={onRegister} className="px-3.5 py-2 rounded-xl">
          <Plus className="w-3.5 h-3.5 text-[#E8A33D]" /> Register Agent
        </Button>
        <Button onClick={onToggleSimulator} className="px-3.5 py-2 rounded-xl">
          {simulatorRunning ? <Pause className="w-3.5 h-3.5 text-[#E8A33D]" /> : <Play className="w-3.5 h-3.5 text-[#4C9A6A]" />}
          {simulatorRunning ? 'Pause Simulator' : 'Start Simulator'}
        </Button>
        <Button onClick={onResumeFleet} className="px-3.5 py-2 rounded-xl">
          <RotateCcw className="w-3.5 h-3.5 text-[#4C9A6A]" /> Resume Fleet
        </Button>
        <Button onClick={onResetAllSpend} className="px-3.5 py-2 rounded-xl">
          <RefreshCw className="w-3.5 h-3.5 text-[#E8A33D]" /> Reset Fleet Spend
        </Button>
        <Button variant="danger" onClick={onFleetStop} className="px-4 py-2 rounded-xl active:scale-95">
          <ShieldAlert className="w-4 h-4" /> Fleet Emergency Stop
        </Button>
      </div>
    </header>
  );
}
