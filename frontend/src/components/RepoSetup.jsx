import React, { useEffect, useState } from "react";

const EMPTY = { repo_root: "", coding_image: "", remote_repo: "" };

/** The last folder of a repository path, for the header button. */
export function repoLabel(repo) {
  if (!repo?.repo_root) return null;
  const parts = String(repo.repo_root).split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] || repo.repo_root;
}

/**
 * Choose the one git repository this chat's model may work on. `value` is
 * { repo_root, coding_image, remote_repo } or null; `onSave` receives the same shape, with an empty
 * repo_root to remove the binding, and may reject with an API error that is shown here.
 */
export function RepoSetup({ value, onSave, legend = "Repository for this chat" }) {
  const [draft, setDraft] = useState({ ...EMPTY, ...(value || {}) });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setDraft({ repo_root: value?.repo_root || "", coding_image: value?.coding_image || "", remote_repo: value?.remote_repo || "" });
  }, [value?.repo_root, value?.coding_image, value?.remote_repo]);

  async function submit(event, next = draft) {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await onSave({
        repo_root: next.repo_root.trim(),
        coding_image: next.coding_image.trim() || null,
        remote_repo: next.remote_repo.trim() || null,
      });
      setSaved(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const set = (key) => (event) => {
    setSaved(false);
    setDraft({ ...draft, [key]: event.target.value });
  };

  return (
    <form className="panel repo-setup" onSubmit={submit} aria-label={legend}>
      <p className="panel-title">{legend}</p>
      <p className="muted small">
        The model can read this repository and ask to change it. It cannot work anywhere else, and every change, commit or command asks you first.
        The folder is on the computer that runs Mucaro and must be inside a folder you have granted.
      </p>
      <label className="field">
        <span>Repository folder</span>
        <input type="text" autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="~/code/my-project" value={draft.repo_root} onChange={set("repo_root")} />
      </label>
      <label className="field">
        <span>Container image for running commands (optional)</span>
        <input type="text" autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="name@sha256:… or sha256:…" value={draft.coding_image} onChange={set("coding_image")} />
      </label>
      <label className="field">
        <span>GitHub repository for pull requests (optional)</span>
        <input type="text" autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="owner/name" value={draft.remote_repo} onChange={set("remote_repo")} />
      </label>
      {error && <p className="error-text" role="alert">{error.status}: {error.message}</p>}
      <div className="button-row">
        {value?.repo_root && (
          <button type="button" className="secondary-button" disabled={busy} onClick={(event) => submit(event, EMPTY)}>
            Remove
          </button>
        )}
        <button type="submit" className="primary-button" disabled={busy || !draft.repo_root.trim()}>
          {busy ? "Checking…" : "Use this repository"}
        </button>
      </div>
      {saved && <p className="small" role="status"><span className="status-chip status-completed">Saved</span></p>}
    </form>
  );
}
