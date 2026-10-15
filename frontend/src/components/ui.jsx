import { useEffect } from 'react';

export function Badge({ className = '', children }) {
  return (
    <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold tracking-wide uppercase ${className}`}>
      {children}
    </span>
  );
}

export function Button({ variant = 'ghost', className = '', ...props }) {
  const variants = {
    ghost: 'bg-[#1C2128] hover:bg-[#252C35] text-[#E6E8EB] border border-[#2D333B]',
    danger: 'bg-[#C1443B] hover:bg-[#A8372F] text-white font-semibold',
    softDanger: 'bg-[#C1443B]/20 hover:bg-[#C1443B]/30 text-[#C1443B] font-bold',
    softOk: 'bg-[#4C9A6A]/20 hover:bg-[#4C9A6A]/30 text-[#4C9A6A] font-bold',
    softViolet: 'bg-[#9B6BD3]/20 hover:bg-[#9B6BD3]/30 text-[#B48BE6] font-bold',
    signal: 'bg-[#E8A33D] hover:bg-[#D6922C] text-[#0B0D10] font-bold',
  };
  return (
    <button
      className={`flex items-center justify-center gap-1.5 rounded-lg transition-all disabled:opacity-50 ${variants[variant]} ${className}`}
      {...props}
    />
  );
}

export function SectionTitle({ icon: Icon, children, right }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <h2 className="text-xs font-mono font-bold text-[#8A93A3] uppercase tracking-wider flex items-center gap-2">
        {Icon && <Icon className="w-4 h-4 text-[#E8A33D]" />} {children}
      </h2>
      {right}
    </div>
  );
}

export function Modal({ title, subtitle, onClose, children, wide = false }) {
  useEffect(() => {
    const handler = (e) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        className={`panel-base w-full ${wide ? 'max-w-2xl' : 'max-w-md'} max-h-[90vh] overflow-y-auto p-6 border border-[#2D333B] shadow-2xl`}
      >
        <h3 className="text-base font-bold text-[#E6E8EB] mb-1">{title}</h3>
        {subtitle && <p className="text-xs text-[#8A93A3] mb-4 font-mono">{subtitle}</p>}
        {children}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="block text-[#E6E8EB] font-semibold mb-1">{label}</span>
      {children}
      {hint && <span className="block text-[10px] text-[#8A93A3] mt-1 font-mono">{hint}</span>}
    </label>
  );
}

// eslint-disable-next-line react/only-export-components
export const inputClass =
  'w-full bg-[#0E1115] border border-[#2D333B] focus:border-[#E8A33D] outline-none rounded-lg p-2 text-[#E6E8EB] font-mono';
