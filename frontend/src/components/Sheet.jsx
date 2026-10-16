import React, { useLayoutEffect, useRef } from "react";

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

// Accessible modal sheet: bottom sheet on phones, side drawer on wide screens.
// Traps focus, closes on Escape or backdrop, and returns focus to the opener.
//
// `onClose` is read through a ref. Callers pass inline arrows, so keying the focus effect on it re-ran the effect
// on every parent render: focus left a text field after each keystroke (on iOS the keyboard closed) and was reset
// to the close button every few seconds while a sheet was open.
export function Sheet({ title, onClose, children, side = "right", labelledBy }) {
  const panel = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);
  const closeRef = useRef(onClose);

  useLayoutEffect(() => {
    closeRef.current = onClose;
  });

  useLayoutEffect(() => {
    const node = panel.current;
    // Initial focus: a field marked data-autofocus, otherwise the dialog itself so a screen reader announces its
    // name. Not the close button, which is first in document order.
    const target = node?.querySelector("[data-autofocus]") || node;
    target?.focus();
    if (target && target !== node && typeof target.select === "function") target.select();
    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      } else if (event.key === "Tab" && node) {
        const items = [...node.querySelectorAll(FOCUSABLE)].filter((el) => !el.disabled);
        if (!items.length) return;
        const firstItem = items[0];
        const lastItem = items[items.length - 1];
        if (event.shiftKey && (document.activeElement === firstItem || document.activeElement === node)) {
          event.preventDefault();
          lastItem.focus();
        } else if (!event.shiftKey && document.activeElement === lastItem) {
          event.preventDefault();
          firstItem.focus();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    const previous = opener.current;
    return () => {
      document.removeEventListener("keydown", onKey);
      if (previous && typeof previous.focus === "function") previous.focus();
    };
  }, []);

  const headingId = labelledBy || `sheet-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="sheet-backdrop" onMouseDown={(event) => event.target === event.currentTarget && closeRef.current()}>
      <section ref={panel} className={`sheet sheet-${side}`} role="dialog" aria-modal="true" aria-labelledby={headingId} tabIndex={-1}>
        <header className="sheet-header">
          <h2 id={headingId}>{title}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label={`Close ${title}`}>
            ✕
          </button>
        </header>
        <div className="sheet-body">{children}</div>
      </section>
    </div>
  );
}
