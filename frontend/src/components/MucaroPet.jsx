import React, { useEffect, useRef, useState } from "react";
import { MUCARO_BRAND_ICON } from "../brandLogo.js";
import { PET_SIZES, normalizeViewportPoint, viewportPoint, uiText } from "../uiPreferences.js";

// This is a truthful static-image fallback for the missing 40-frame sprite atlas.
// It can be docked or manually dragged. No autonomous roaming/action-following is
// claimed until the original atlas and target-geometry regressions are available.
export function MucaroPet({ prefs, onChange, runtime, locale, open, onOpenChange }) {
  const [view, setView] = useState(() => ({
    width: window.visualViewport?.width || window.innerWidth,
    height: window.visualViewport?.height || window.innerHeight,
  }));
  const [dragging, setDragging] = useState(false);
  const [live, setLive] = useState(null);
  const drag = useRef(null), suppressClick = useRef(false);
  const panelClose = useRef(null), opener = useRef(null);
  const size = PET_SIZES[prefs.size] || 44;
  const reduced = prefs.reducedMotion;
  const motion = !reduced && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const defaultPoint = viewportPoint(prefs.position, view.width, view.height, size);
  const coords = live || defaultPoint;

  useEffect(() => {
    const update = () => setView({ width: window.visualViewport?.width || window.innerWidth, height: window.visualViewport?.height || window.innerHeight });
    window.addEventListener("resize", update);
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    return () => {
      window.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("scroll", update);
    };
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    opener.current = document.activeElement;
    panelClose.current?.focus();
    const escape = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onOpenChange(false);
    };
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("keydown", escape);
      if (opener.current?.isConnected) opener.current.focus();
    };
  }, [open, onOpenChange]);

  const patch = (changes) => onChange({ ...prefs, ...changes });
  const onDown = (event) => {
    if (event.button !== 0 && event.pointerType !== "touch") return;
    if (!prefs.floating) return;
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: coords.x, top: coords.y, moved: false };
    suppressClick.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onMove = (event) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const dx = event.clientX - current.x, dy = event.clientY - current.y;
    if (Math.hypot(dx, dy) > 5) current.moved = true;
    if (!current.moved) return;
    const next = normalizeViewportPoint(current.left + dx, current.top + dy, view.width, view.height, size);
    setLive(viewportPoint(next, view.width, view.height, size));
    setDragging(true);
  };
  const finish = (event, cancelled = false) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    drag.current = null;
    if (current.moved) {
      suppressClick.current = true;
      if (!cancelled) {
        const normalized = normalizeViewportPoint(
          current.left + event.clientX - current.x,
          current.top + event.clientY - current.y,
          view.width, view.height, size
        );
        patch({ position: normalized });
      }
    }
    setDragging(false);
    setLive(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  return (
    <>
      {prefs.visible && prefs.floating && (
        <button type="button" className="mucaro-floating-pet"
          style={{ left: coords.x, top: coords.y, width: size, height: size }}
          data-pet-state={runtime.state}
          data-pet-observation={runtime.source}
          data-dragging={dragging}
          data-motion={motion}
          aria-label={uiText(locale, "pet") + ": " + runtime.label}
          title={runtime.label}
          onPointerDown={onDown} onPointerMove={onMove}
          onPointerUp={finish} onPointerCancel={(event) => finish(event, true)}
          onClick={() => { if (suppressClick.current) { suppressClick.current = false; return; } onOpenChange(true); }}>
          <img src={MUCARO_BRAND_ICON} width={size} height={size} alt="" draggable="false" />
        </button>
      )}
      {open && (
        <section className="mucaro-pet-panel" role="region" aria-label={uiText(locale, "pet") + " controls"}>
          <header>
            <strong>{uiText(locale, "pet")}</strong>
            <button ref={panelClose} type="button" className="icon-button" aria-label="Close owl controls" onClick={() => onOpenChange(false)}>×</button>
          </header>
          <div className="mucaro-pet-state" data-pet-state={runtime.state}>
            <img src={MUCARO_BRAND_ICON} alt="" width="36" height="36" />
            <div><strong>{runtime.state}</strong><span className="muted small block">{runtime.label} · {runtime.source}</span></div>
          </div>
          <label className="switch"><input type="checkbox" checked={prefs.visible} onChange={(event) => patch({ visible:event.target.checked })}/><span>{uiText(locale, "petHidden")}</span></label>
          <label className="switch"><input type="checkbox" checked={prefs.floating} onChange={(event) => patch({ floating:event.target.checked })}/><span>{uiText(locale, "floating")}</span></label>
          <label className="switch"><input type="checkbox" checked={prefs.reducedMotion} onChange={(event) => patch({ reducedMotion:event.target.checked })}/><span>{uiText(locale, "reducedMotion")}</span></label>
          <label className="field">
            <span>{uiText(locale, "petSize")}</span>
            <select value={prefs.size} onChange={(event) => patch({ size:event.target.value })}>
              {["small","medium","large"].map((name) => <option key={name} value={name}>{uiText(locale,name)}</option>)}
            </select>
          </label>
          {prefs.floating && <button type="button" className="secondary-button" onClick={() => patch({ floating:false, position:null })}>{uiText(locale, "dock")}</button>}
          <p className="muted small">Static fallback: the draft's animated sprite atlas was not included in the ZIP. States reflect observed local service/run events.</p>
        </section>
      )}
    </>
  );
}
