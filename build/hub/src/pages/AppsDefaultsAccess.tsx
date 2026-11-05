import { useState } from 'react';
import { openRowynSettings, type SettingTarget } from '../services/rowynSettings';

type Action = { id: SettingTarget; title: string; description: string };
const actions: Action[] = [
  { id: 'defaults', title: 'Default applications', description: 'Choose default apps.' },
  { id: 'file-associations', title: 'File associations', description: 'Choose apps for file types.' },
  { id: 'flatpak-permissions', title: 'Flatpak permissions', description: 'Review app permissions and persistent overrides.' },
];

export function AppsDefaultsAccess() {
  const [pending, setPending] = useState<SettingTarget | null>(null);
  const [result, setResult] = useState<{ target: SettingTarget; text: string; error: boolean } | null>(null);

  async function launch(target: SettingTarget) {
    setPending(target);
    setResult(null);
    try {
      const message = await openRowynSettings(target);
      setResult({ target, text: message, error: false });
    } catch (error) {
      setResult({ target, text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      setPending(null);
    }
  }

  return (
    <section className="glass dashboard-card rowyn-mvp" aria-label="Defaults and access">
      <h2>Defaults &amp; Access</h2>
      <p>Portal-mediated file selection can grant access, and users may choose persistent portal permissions. Review broad persistent overrides separately.</p>
      <div>
        {actions.map(({ id, title, description }) => (
          <article key={id}>
            <h3>{title}</h3>
            <p>{description}</p>
            <button className="action-button" disabled={pending !== null} onClick={() => void launch(id)}>
              {pending === id ? 'Opening…' : 'Open system settings'}
            </button>
            {result?.target === id && (
              <p role={result.error ? 'alert' : 'status'}>{result.text}</p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
