import test from "node:test";
import assert from "node:assert/strict";
import { UI_DEFAULTS, normalizeUIPrefs, readUIPrefs, writeUIPrefs, effectiveTheme, uiText, uiFlag, PET_SIZES, viewportPoint, normalizeViewportPoint } from "./uiPreferences.js";
import { derivePetRuntime, observationSignature } from "./petRuntime.js";

test("invalid or malformed persisted preferences fail closed", () => {
  for (const raw of [null, "bad", ["es"], 3, { theme: "blue", language: "xx", pet: { size: "giant", position: { x: Infinity, y: 2 } } }]) {
    const prefs = normalizeUIPrefs(raw);
    assert.equal(prefs.theme, "auto");
    assert.equal(prefs.language, "en");
    assert.equal(prefs.pet.position, null);
  }
  const blocked = { getItem() { throw Error("storage blocked"); } };
  assert.deepEqual(readUIPrefs(blocked), normalizeUIPrefs(null));
});
test("preferences conserve enabled values but clamp corrupted coordinates", () => {
  const prefs = normalizeUIPrefs({ language: "es", theme: "dark", pet: { visible:false, floating:true, reducedMotion:true, size:"large", position:{x:-3,y:8} } });
  assert.deepEqual(prefs, { language:"es", theme:"dark", pet:{visible:false,floating:true,reducedMotion:true,size:"large",position:{x:0,y:1}} });
});
test("persistence writes canonical shape and handles denied writes", () => {
  let value; const ok={setItem(k,v){value=[k,v];}};
  assert.equal(writeUIPrefs({language:"es",theme:"light"},ok),true);
  assert.equal(JSON.parse(value[1]).language,"es");
  assert.equal(writeUIPrefs(UI_DEFAULTS,{setItem(){throw Error("denied");}}),false);
});
test("explicit themes override system while auto follows it", () => {
  assert.equal(effectiveTheme("light",true),"light"); assert.equal(effectiveTheme("dark",false),"dark");
  assert.equal(effectiveTheme("auto",true),"dark"); assert.equal(effectiveTheme("auto",false),"light");
});
test("locale labels and flags are bounded to supported locales", () => {
  assert.equal(uiText("es","settings"),"Ajustes"); assert.equal(uiText("en","settings"),"Settings");
  assert.equal(uiText("zz","settings"),"Settings"); assert.equal(uiFlag("es"),"🇵🇷"); assert.equal(uiFlag("en"),"🇺🇸");
});
test("owl position round-trips and remains bounded", () => {
  const size=PET_SIZES.medium, p={x:.27,y:.81}; const px=viewportPoint(p,390,844,size);
  const normalized=normalizeViewportPoint(px.x,px.y,390,844,size);
  assert.ok(Math.abs(normalized.x-p.x)<1e-9); assert.ok(Math.abs(normalized.y-p.y)<1e-9);
  assert.deepEqual(normalizeViewportPoint(-100,9999,390,844,size),{x:0,y:1});
  const tiny=viewportPoint({x:1,y:1},10,10,72); assert.ok(tiny.x>=0&&tiny.x<=10&&tiny.y>=0&&tiny.y<=10);
});
test("owl runtime state requires an observed run id and supported run state", () => {
  assert.deepEqual(derivePetRuntime("ok",{runState:"RUNNING"}),{state:"IDLE",label:"Ready",source:"local"});
  assert.equal(derivePetRuntime("ok",{runId:"r1",runState:"RUNNING"}).state,"WORK");
  assert.equal(derivePetRuntime("ok",{runId:"r1",runState:"AWAITING_APPROVAL"}).state,"NOTIFY");
  assert.equal(derivePetRuntime("ok",{runId:"r1",runState:"COMPLETED"}).state,"HAPPY");
  assert.equal(derivePetRuntime("ok",{runId:"r1",runState:"MADE_UP"}).state,"IDLE");
});
test("health failure has precedence over stale run status", () => {
  const state=derivePetRuntime("error",{runId:"r1",runState:"COMPLETED"});
  assert.equal(state.state,"NOTIFY"); assert.equal(state.source,"health");
});
test("runtime observation identity is explicit, not nearest-state inference", () => {
  assert.equal(observationSignature({runId:"r1",runState:"RUNNING"}),"r1:RUNNING");
  assert.equal(observationSignature({runId:"r1"}),null); assert.equal(observationSignature(null),null);
});
