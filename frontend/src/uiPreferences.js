export const UI_PREFS_KEY = "mucaro.ui.preferences.v2";
export const UI_DEFAULTS = Object.freeze({
  language: "en",
  theme: "auto",
  density: "comfortable",
  accent: "teal",
  motion: "system",
  general: { startRoute: "chat" },
  notifications: { runComplete: true, errors: true, approvals: true },
  voice: { enabled: true, rate: 1 },
  personalization: {
    style: "balanced",
    warmth: 0.5,
    concision: 0.5,
    enthusiasm: 0.35,
    emoji: 0.1,
    quickResponses: false,
    suggestedFollowups: true,
    customInstructions: "",
  },
  owl: {
    visible: true,
    floating: false,
    reducedMotion: false,
    size: "small",
    position: null,
  },
});
const clamp = (value, fallback = 0.5) => Number.isFinite(value) ? Math.min(1, Math.max(0, Number(value))) : fallback;
export function normalizeUIPrefs(value) {
  const raw = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const personalization = raw.personalization && typeof raw.personalization === "object" ? raw.personalization : {};
  const owl = raw.owl && typeof raw.owl === "object" ? raw.owl : {};
  const pos = owl.position && Number.isFinite(owl.position.x) && Number.isFinite(owl.position.y)
    ? { x: clamp(owl.position.x, 1), y: clamp(owl.position.y, 0.5) } : null;
  return {
    language: ["en", "es"].includes(raw.language) ? raw.language : UI_DEFAULTS.language,
    theme: ["auto", "light", "dark"].includes(raw.theme) ? raw.theme : UI_DEFAULTS.theme,
    density: ["compact", "comfortable", "roomy"].includes(raw.density) ? raw.density : UI_DEFAULTS.density,
    accent: ["teal", "blue", "violet"].includes(raw.accent) ? raw.accent : UI_DEFAULTS.accent,
    motion: ["system", "full", "reduced"].includes(raw.motion) ? raw.motion : UI_DEFAULTS.motion,
    general: { startRoute: ["chat","sources","documents","memory","provenance","settings"].includes(raw.general?.startRoute) ? raw.general.startRoute : "chat" },
    notifications: {
      runComplete: raw.notifications?.runComplete !== false,
      errors: raw.notifications?.errors !== false,
      approvals: raw.notifications?.approvals !== false,
    },
    voice: {
      enabled: raw.voice?.enabled !== false,
      rate: Math.min(2, Math.max(0.5, Number(raw.voice?.rate) || 1)),
    },
    personalization: {
      style: ["balanced", "concise", "detailed", "analytical"].includes(personalization.style) ? personalization.style : "balanced",
      warmth: clamp(personalization.warmth, 0.5),
      concision: clamp(personalization.concision, 0.5),
      enthusiasm: clamp(personalization.enthusiasm, 0.35),
      emoji: clamp(personalization.emoji, 0.1),
      quickResponses: personalization.quickResponses === true,
      suggestedFollowups: personalization.suggestedFollowups !== false,
      customInstructions: typeof personalization.customInstructions === "string" ? personalization.customInstructions.slice(0, 12000) : "",
    },
    owl: {
      visible: owl.visible !== false,
      floating: owl.floating === true,
      reducedMotion: owl.reducedMotion === true,
      size: ["small", "medium", "large"].includes(owl.size) ? owl.size : "small",
      position: pos,
    },
  };
}
export function readUIPrefs(storage = globalThis.localStorage) {
  try { return normalizeUIPrefs(JSON.parse(storage.getItem(UI_PREFS_KEY) ?? "null")); }
  catch { return normalizeUIPrefs(null); }
}
export function writeUIPrefs(prefs, storage = globalThis.localStorage) {
  try { storage.setItem(UI_PREFS_KEY, JSON.stringify(normalizeUIPrefs(prefs))); return true; }
  catch { return false; }
}
export function effectiveTheme(theme, systemDark) {
  return theme === "dark" || (theme === "auto" && systemDark) ? "dark" : "light";
}
export function effectiveReducedMotion(prefs, systemReduced) {
  return prefs.motion === "reduced" || (prefs.motion === "system" && systemReduced) || prefs.owl.reducedMotion;
}
export const OWL_SIZES = Object.freeze({ small: 44, medium: 58, large: 74 });
export function viewportPoint(position, width, height, size) {
  const w = Math.max(0, Number(width) || 0), h = Math.max(0, Number(height) || 0), s = Math.max(0, Number(size) || 0);
  const gapX = Math.min(8, Math.max(0, (w - s) / 2)), gapY = Math.min(8, Math.max(0, (h - s) / 2));
  const maxX = Math.max(0, w - s - 2 * gapX), maxY = Math.max(0, h - s - 2 * gapY);
  return { x: gapX + clamp(position?.x, 1) * maxX, y: gapY + clamp(position?.y, 0.5) * maxY };
}
export function normalizeViewportPoint(x, y, width, height, size) {
  const w = Math.max(0, Number(width) || 0), h = Math.max(0, Number(height) || 0), s = Math.max(0, Number(size) || 0);
  const gapX = Math.min(8, Math.max(0, (w - s) / 2)), gapY = Math.min(8, Math.max(0, (h - s) / 2));
  const maxX = Math.max(0, w - s - 2 * gapX), maxY = Math.max(0, h - s - 2 * gapY);
  return { x: maxX ? clamp((x - gapX) / maxX, 0) : 0, y: maxY ? clamp((y - gapY) / maxY, 0) : 0 };
}
export const STRINGS = {
  en: { chat:"Chat",sources:"Sources",documents:"Documents",memory:"Memory",provenance:"Provenance",settings:"Settings",tasks:"Tasks",newChat:"New chat",search:"Search",pinned:"Pinned",recent:"Recent",appearance:"Appearance",personalization:"Personalization",general:"General",notifications:"Notifications",voice:"Voice",security:"Security",storage:"Storage",data:"Data controls",diagnostics:"Diagnostics",help:"Help",about:"About" },
  es: { chat:"Chat",sources:"Fuentes",documents:"Documentos",memory:"Memoria",provenance:"Procedencia",settings:"Ajustes",tasks:"Tareas",newChat:"Nuevo chat",search:"Buscar",pinned:"Fijados",recent:"Recientes",appearance:"Apariencia",personalization:"Personalización",general:"General",notifications:"Notificaciones",voice:"Voz",security:"Seguridad",storage:"Almacenamiento",data:"Controles de datos",diagnostics:"Diagnóstico",help:"Ayuda",about:"Acerca de" },
};
export function uiText(locale, key) { return STRINGS[locale]?.[key] || STRINGS.en[key] || key; }
