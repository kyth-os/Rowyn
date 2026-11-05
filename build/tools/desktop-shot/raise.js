// KWin scripting helper: bring the Hub forward so a portal capture shows it.
// Loaded by shot.sh through org.kde.kwin.Scripting on the session bus.
const windows = workspace.windowList ? workspace.windowList() : [];
for (const w of windows) {
  const caption = w.caption || "";
  if (caption.indexOf("Rowyn Hub") !== -1) {
    if (w.minimized) w.minimized = false;
    workspace.activeWindow = w;
  }
}
