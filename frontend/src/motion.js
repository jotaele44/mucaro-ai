import { useEffect, useRef, useState } from "react";

export const MOTION = Object.freeze({
  press: 80,
  hover: 110,
  drawerOpen: 220,
  drawerClose: 180,
  pagePush: 240,
  pagePop: 220,
  sheetOpen: 280,
  sheetClose: 210,
  tab: 140,
  accordion: 180,
  checklist: 140,
  scrim: 190,
});

export function useSystemReducedMotion() {
  const query = typeof window !== "undefined" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const [value, setValue] = useState(Boolean(query?.matches));
  useEffect(() => {
    if (!query) return undefined;
    const sync = () => setValue(query.matches);
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, [query]);
  return value;
}

export function useVisualViewport() {
  const read = () => ({
    width: window.visualViewport?.width || window.innerWidth,
    height: window.visualViewport?.height || window.innerHeight,
    offsetTop: window.visualViewport?.offsetTop || 0,
    offsetLeft: window.visualViewport?.offsetLeft || 0,
    scale: window.visualViewport?.scale || 1,
  });
  const [state, setState] = useState(read);
  const frame = useRef(0);
  useEffect(() => {
    const viewport = window.visualViewport;
    const schedule = () => {
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => setState(read()));
    };
    window.addEventListener("resize", schedule);
    viewport?.addEventListener("resize", schedule);
    viewport?.addEventListener("scroll", schedule);
    return () => {
      cancelAnimationFrame(frame.current);
      window.removeEventListener("resize", schedule);
      viewport?.removeEventListener("resize", schedule);
      viewport?.removeEventListener("scroll", schedule);
    };
  }, []);
  return state;
}

export function useEdgeSwipeBack({ enabled = true, onCommit, edgePx = 28, threshold = 0.33, velocity = 0.55 } = {}) {
  const [progress, setProgress] = useState(0);
  const gesture = useRef(null);
  const onPointerDown = (event) => {
    if (!enabled || event.pointerType === "mouse" || event.clientX > edgePx || event.isPrimary === false) return;
    gesture.current = { id: event.pointerId, startX: event.clientX, startY: event.clientY, lastX: event.clientX, lastT: event.timeStamp, vx: 0, active: true };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event) => {
    const g = gesture.current;
    if (!g || g.id !== event.pointerId || !g.active) return;
    const dx = Math.max(0, event.clientX - g.startX), dy = Math.abs(event.clientY - g.startY);
    if (dy > dx * 1.15 && dx < 18) { g.active = false; setProgress(0); return; }
    const dt = Math.max(1, event.timeStamp - g.lastT);
    g.vx = (event.clientX - g.lastX) / dt;
    g.lastX = event.clientX; g.lastT = event.timeStamp;
    setProgress(Math.min(1, dx / Math.max(1, window.innerWidth)));
  };
  const finish = (event) => {
    const g = gesture.current;
    if (!g || g.id !== event.pointerId) return;
    const commit = g.active && (progress >= threshold || g.vx >= velocity);
    gesture.current = null;
    setProgress(0);
    if (commit) onCommit?.();
  };
  return { progress, bind: { onPointerDown, onPointerMove, onPointerUp: finish, onPointerCancel: finish } };
}
