// Offline-first GUI preferences adapted from the user-provided Múcaro GUI draft.
// Settings describe the local shell only; they are not backend capability claims.
export const UI_PREFS_KEY = "mucaro.ui.preferences.v1";
export const UI_DEFAULTS = Object.freeze({
  language: "en",
  theme: "auto",
  pet: { visible: true, floating: false, position: null, reducedMotion: false, size: "small" },
});
const bounded = (n) => typeof n === "number" && Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : null;
export function normalizeUIPrefs(value) {
  const raw = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const pet = raw.pet && typeof raw.pet === "object" && !Array.isArray(raw.pet) ? raw.pet : {};
  const x = bounded(pet.position?.x), y = bounded(pet.position?.y);
  return {
    language: ["en", "es"].includes(raw.language) ? raw.language : UI_DEFAULTS.language,
    theme: ["auto", "light", "dark"].includes(raw.theme) ? raw.theme : UI_DEFAULTS.theme,
    pet: {
      visible: pet.visible !== false,
      floating: pet.floating === true,
      position: x !== null && y !== null ? { x, y } : null,
      reducedMotion: pet.reducedMotion === true,
      size: ["small", "medium", "large"].includes(pet.size) ? pet.size : "small",
    },
  };
}
export function readUIPrefs(storage = globalThis.localStorage) {
  try { return normalizeUIPrefs(JSON.parse(storage.getItem(UI_PREFS_KEY) ?? "null")); }
  catch { return normalizeUIPrefs(null); }
}
export function writeUIPrefs(prefs, storage = globalThis.localStorage) {
  const validated = normalizeUIPrefs(prefs);
  try { storage.setItem(UI_PREFS_KEY, JSON.stringify(validated)); return true; }
  catch { return false; }
}
export function effectiveTheme(theme, systemDark) {
  return theme === "dark" || (theme !== "light" && Boolean(systemDark)) ? "dark" : "light";
}
// Only currently implemented navigational labels have translations. Existing
// backend text/other workspaces remain source-authored and are not claimed bilingual.
const STRINGS = {
  en: {
    chat:"Chat",sources:"Sources",memory:"Memory",provenance:"Provenance",settings:"Settings",
    tasks:"Background tasks",home:"Múcaro home",newChat:"New chat",
    filter:"Filter conversations",noMatches:"No matches.",noChats:"No conversations yet.",
    collapse:"Collapse contextual sidebar",expand:"Expand contextual sidebar",
    appearance:"Appearance and language",language:"Language",theme:"Theme",
    light:"Light",dark:"Dark",auto:"System",pet:"Múcaro owl",petHidden:"Show Múcaro owl",
    petVisible:"Hide Múcaro owl",floating:"Float over workspace",dock:"Dock in sidebar",
    reducedMotion:"Reduce owl motion",petSize:"Owl size",small:"Small",medium:"Medium",large:"Large",
    sectionOverview:"Overview",sectionImport:"Import files",sectionLibrary:"Source library",
    sectionAdd:"Add memory",sectionSaved:"Saved memories",sectionAudit:"Action history",
    sectionModel:"Answer model",sectionRetrieval:"Retrieval",sectionAccess:"Local access",
    langSwitch:"Switch language to Spanish",settingsNote:"Navigation and these settings are translated. Existing workspace and backend content may remain in its source language.",
  },
  es: {
    chat:"Chat",sources:"Fuentes",memory:"Memoria",provenance:"Procedencia",settings:"Ajustes",
    tasks:"Tareas en segundo plano",home:"Inicio de Múcaro",newChat:"Nuevo chat",
    filter:"Filtrar conversaciones",noMatches:"Sin resultados.",noChats:"Todavía no hay conversaciones.",
    collapse:"Ocultar panel contextual",expand:"Mostrar panel contextual",
    appearance:"Apariencia e idioma",language:"Idioma",theme:"Tema",
    light:"Claro",dark:"Oscuro",auto:"Sistema",pet:"Múcaro búho",petHidden:"Mostrar búho de Múcaro",
    petVisible:"Ocultar búho de Múcaro",floating:"Flotar sobre el área de trabajo",dock:"Acoplar en la barra lateral",
    reducedMotion:"Reducir movimiento del búho",petSize:"Tamaño del búho",small:"Pequeño",medium:"Mediano",large:"Grande",
    sectionOverview:"Resumen",sectionImport:"Importar archivos",sectionLibrary:"Biblioteca de fuentes",
    sectionAdd:"Añadir recuerdo",sectionSaved:"Memorias guardadas",sectionAudit:"Historial de acciones",
    sectionModel:"Modelo de respuesta",sectionRetrieval:"Recuperación",sectionAccess:"Acceso local",
    langSwitch:"Cambiar idioma a inglés",settingsNote:"La navegación y estos ajustes están traducidos. Otros espacios y mensajes del servicio pueden conservar su idioma original.",
  },
};
export function uiText(locale, key) { return STRINGS[locale]?.[key] || STRINGS.en[key] || key; }
export function uiFlag(locale) { return locale === "es" ? "🇵🇷" : "🇺🇸"; }
export const PET_SIZES = Object.freeze({ small: 44, medium: 56, large: 72 });
export function viewportPoint(position, width, height, size) {
  const w = Math.max(0, Number.isFinite(width) ? width : 0);
  const h = Math.max(0, Number.isFinite(height) ? height : 0);
  const s = Math.max(0, Number.isFinite(size) ? size : 0);
  const gapX = Math.min(8, Math.max(0, (w - s) / 2));
  const gapY = Math.min(8, Math.max(0, (h - s) / 2));
  const maxX = Math.max(0, w - s - 2 * gapX), maxY = Math.max(0, h - s - 2 * gapY);
  return { x: gapX + (bounded(position?.x) ?? 1) * maxX, y: gapY + (bounded(position?.y) ?? 0.5) * maxY };
}
export function normalizeViewportPoint(x, y, width, height, size) {
  const w = Math.max(0, Number.isFinite(width) ? width : 0);
  const h = Math.max(0, Number.isFinite(height) ? height : 0);
  const s = Math.max(0, Number.isFinite(size) ? size : 0);
  const gapX = Math.min(8, Math.max(0, (w - s) / 2));
  const gapY = Math.min(8, Math.max(0, (h - s) / 2));
  const maxX = Math.max(0, w - s - 2 * gapX), maxY = Math.max(0, h - s - 2 * gapY);
  const clamp = (v) => Math.min(1, Math.max(0, v));
  return { x: maxX > 0 && Number.isFinite(x) ? clamp((x - gapX) / maxX) : 0,
    y: maxY > 0 && Number.isFinite(y) ? clamp((y - gapY) / maxY) : 0 };
}
