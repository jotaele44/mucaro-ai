// Copying text. `navigator.clipboard` exists only in secure contexts (https, or localhost), so a phone that opens the
// computer at http://<its network address> has no such API. The selection-based copy still works there, so it is the
// fallback, and a failure says why instead of blaming a permission that was never asked for.

export const COPY_INSECURE = "insecure";
export const COPY_DENIED = "denied";

function selectionCopy(text) {
  const field = document.createElement("textarea");
  field.value = text;
  field.readOnly = true; // keeps the on-screen keyboard closed
  field.setAttribute("aria-hidden", "true");
  // 16px stops iOS from zooming the page when the field takes focus.
  field.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;font-size:16px;";
  const previous = document.activeElement;
  document.body.appendChild(field);
  let copied = false;
  try {
    field.focus({ preventScroll: true });
    field.select();
    field.setSelectionRange(0, text.length); // select() alone does not select on iOS
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  } finally {
    field.remove();
    if (previous instanceof HTMLElement) previous.focus({ preventScroll: true });
  }
  return copied;
}

/** Resolves `{ ok: true }`, or `{ ok: false, reason }` with COPY_INSECURE or COPY_DENIED. Never rejects. */
export async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return { ok: true };
    } catch {
      // Refused here (no user gesture, a permission policy): the selection-based copy below may still work.
    }
  }
  if (selectionCopy(text)) return { ok: true };
  return { ok: false, reason: window.isSecureContext === false ? COPY_INSECURE : COPY_DENIED };
}

export function copyFailureMessage(result) {
  return result.reason === COPY_INSECURE
    ? "Copy failed: this browser copies only over a secure connection. Select the text and copy it by hand."
    : "Copy failed: clipboard access was not allowed.";
}
