import React, { useEffect, useRef, useState } from "react";
import { MOTION, useVisualViewport } from "../motion.js";

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function Sheet({ title, onClose, children, side = "right", labelledBy, size = "medium", draggable = true, showGrabber = true }) {
  const panel = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);
  const closeRef = useRef(onClose);
  const drag = useRef(null);
  const [dragY, setDragY] = useState(0);
  const viewport = useVisualViewport();

  useEffect(() => { closeRef.current = onClose; });
  useEffect(() => {
    const node = panel.current;
    const target = node?.querySelector("[data-autofocus]") || node;
    target?.focus();
    if (target && target !== node && typeof target.select === "function") target.select();
    const beforeOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      } else if (event.key === "Tab" && node) {
        const items = [...node.querySelectorAll(FOCUSABLE)].filter((el) => !el.disabled && el.getClientRects().length);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === node)) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    const previous = opener.current;
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = beforeOverflow;
      if (previous && typeof previous.focus === "function" && previous.isConnected) previous.focus();
    };
  }, []);

  const canDrag = draggable && side !== "center";
  function pointerDown(event) {
    if (!canDrag || event.isPrimary === false) return;
    drag.current = { id:event.pointerId, y:event.clientY, lastY:event.clientY, lastT:event.timeStamp, vy:0 };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function pointerMove(event) {
    const g=drag.current;
    if (!g || g.id!==event.pointerId) return;
    const dt=Math.max(1,event.timeStamp-g.lastT);
    g.vy=(event.clientY-g.lastY)/dt; g.lastY=event.clientY; g.lastT=event.timeStamp;
    setDragY(Math.max(0,event.clientY-g.y));
  }
  function pointerEnd(event) {
    const g=drag.current;
    if (!g || g.id!==event.pointerId) return;
    const height=panel.current?.getBoundingClientRect().height || 1;
    const dismiss=dragY/height>0.28 || g.vy>0.65;
    drag.current=null;
    if (dismiss) closeRef.current();
    else setDragY(0);
  }

  const headingId = labelledBy || `sheet-${title.replace(/\W+/g, "-").toLowerCase()}`;
  const mobileBottom = side !== "center";
  return (
    <div className="sheet-backdrop" style={{ "--vvh": `${viewport.height}px`, "--sheet-duration": `${MOTION.sheetOpen}ms` }}
      onPointerDown={(event) => event.target === event.currentTarget && closeRef.current()}>
      <section ref={panel}
        className={`sheet sheet-${side} sheet-size-${size} ${dragY ? "sheet-dragging" : ""}`}
        style={mobileBottom ? { transform: dragY ? `translate3d(0,${dragY}px,0)` : undefined } : undefined}
        role="dialog" aria-modal="true" aria-labelledby={headingId} tabIndex={-1}>
        {showGrabber && side !== "center" && (
          <div className="sheet-grabber-zone" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerEnd} onPointerCancel={pointerEnd}>
            <span className="sheet-grabber" aria-hidden="true" />
          </div>
        )}
        <header className="sheet-header">
          <h2 id={headingId}>{title}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label={`Close ${title}`}>✕</button>
        </header>
        <div className="sheet-body">{children}</div>
      </section>
    </div>
  );
}
