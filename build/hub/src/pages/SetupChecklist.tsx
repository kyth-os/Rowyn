import { useState } from 'react';
import { CHECKLIST_ITEMS, loadChecklist, saveChecklist, setChecklistItem, type ChecklistState } from '../services/rowynSetupChecklistModel';
import '../styles/rowynSetupChecklist.css';

export function SetupChecklist({ navigate }: { navigate: (route: string) => void }) {
  const [state, setState] = useState<ChecklistState>(() => loadChecklist());
  const [persistenceWarning, setPersistenceWarning] = useState(false);

  function update(next: ChecklistState) {
    setState(next);
    setPersistenceWarning(!saveChecklist(next));
  }

  return (
    <section className="setup-checklist glass dashboard-card" aria-label="First run checklist">
      <header>
        <div>
          <h2>Getting started</h2>
          <p>Optional steps you can complete when ready.</p>
        </div>
        <button className="action-button" onClick={() => update({ ...state, dismissed: !state.dismissed })}>
          {state.dismissed ? 'Resume checklist' : 'Hide checklist'}
        </button>
      </header>
      {persistenceWarning && <p role="status">Preferences could not be saved; changes apply for this session only.</p>}
      {!state.dismissed && (
        <ul>
          {CHECKLIST_ITEMS.map(item => (
            <li key={item.id}>
              <span>{item.label}</span>
              <button className="action-button" onClick={() => navigate(item.route)}>Open</button>
              <button className="action-button" onClick={() => update(setChecklistItem(state, item.id, 'completed'))}>Done</button>
              <button className="action-button" onClick={() => update(setChecklistItem(state, item.id, 'skipped'))}>Skip</button>
              {(state.completed.includes(item.id) || state.skipped.includes(item.id)) && (
                <small>{state.completed.includes(item.id) ? 'Completed manually' : 'Skipped'}</small>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
