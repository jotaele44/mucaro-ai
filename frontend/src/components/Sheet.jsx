import React, { useEffect, useRef } from "react";

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

// Accessible modal sheet: bottom sheet on phones, side drawer on wide screens.
// Traps focus, closes on Escape or backdrop, and returns focus to the opener.
export function Sheet({ title, onClose, children, side = "right", labelledBy }) {
  const panel = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);

  useEffect(() => {
    const node = panel.current;
    const first = node?.querySelector(FOCUSABLE);
    (first || node)?.focus();
    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      } else if (event.key === "Tab" && node) {
        const items = [...node.querySelectorAll(FOCUSABLE)].filter((el) => !el.disabled);
        if (!items.length) return;
        const firstItem = items[0];
        const lastItem = items[items.length - 1];
        if (event.shiftKey && document.activeElement === firstItem) {
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
  }, [onClose]);

  const headingId = labelledBy || `sheet-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="sheet-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
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
