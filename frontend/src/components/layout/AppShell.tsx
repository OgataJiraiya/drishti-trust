import { useState, type ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { Activity, FileText, FileWarning, LayoutDashboard, Menu, Network, RefreshCw, ServerCog, X } from 'lucide-react';
import type { Assessment, BackendState } from '../../api/types';
import { Badge } from '../ui';

const nav = [
  ['/', 'Overview', LayoutDashboard], ['/findings', 'Findings', FileWarning],
  ['/distribution', 'Distribution Shift', Activity], ['/graph', 'Assurance Graph', Network],
  ['/reports', 'Reports', FileText], ['/system', 'System', ServerCog],
] as const;

export function AppShell({ children, assessments, selected, onSelect, backend, demo }: { children: ReactNode; assessments: Assessment[]; selected: string; onSelect: (id: string) => void; backend: BackendState; demo: boolean }) {
  const [open, setOpen] = useState(false);
  const current = assessments.find((item) => item.assessment_id === selected);
  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to main content</a>
    <header className="topbar">
      <button className="mobile-menu" onClick={() => setOpen(!open)} aria-label="Toggle navigation" aria-expanded={open}>{open ? <X /> : <Menu />}</button>
      <div className="command-brand"><strong>Drishti Trust</strong><i /><span>Local Workstation</span></div>
      <div className="context"><label htmlFor="assessment-select">Assessment</label><select id="assessment-select" value={selected} onChange={(event) => onSelect(event.target.value)}><option value="">{assessments.length ? 'Current context' : 'No active assessment'}</option>{assessments.map((item) => <option key={item.assessment_id} value={item.assessment_id}>{item.name} · {item.assessment_id}</option>)}</select></div>
      <div className="backend"><span className={`status-dot ${backend.toLowerCase()}`} /><strong>{backend} / LOCAL</strong></div>
      {current && <div className="top-assessment"><span>ASSESSMENT ID</span><code>{current.assessment_id}</code></div>}
      {demo && <Badge tone="demo">DEMO DATA</Badge>}
    </header>
    <aside className={open ? 'sidebar open' : 'sidebar'} aria-label="Primary">
      <div className="side-identity"><strong>Drishti Trust</strong><small>LOCAL WORKSTATION</small></div>
      <nav>{nav.map(([to, label, Icon]) => <NavLink key={to} to={to} end={to === '/'} onClick={() => setOpen(false)}><Icon /><span>{label}</span></NavLink>)}</nav>
      <div className="sidebar-footer"><small>{backend === 'ONLINE' ? '● Local backend online' : '⌁ Offline Status'}</small><button className="refresh-assessment" onClick={() => window.location.reload()}><RefreshCw /> REFRESH ASSESSMENT</button></div>
    </aside>
    {open && <button className="scrim" aria-label="Close navigation" onClick={() => setOpen(false)} />}
    <main id="main">{children}</main>
  </div>;
}
