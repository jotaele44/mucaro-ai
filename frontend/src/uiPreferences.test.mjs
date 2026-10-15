import test from "node:test";
import assert from "node:assert/strict";
import { UI_DEFAULTS, effectiveReducedMotion, effectiveTheme, normalizeUIPrefs, normalizeViewportPoint, readUIPrefs, viewportPoint, writeUIPrefs } from "./uiPreferences.js";

test("malformed persisted UI preferences fail closed",()=>{
  for(const value of [null,"bad",[],3,{theme:"purple",language:"xx",motion:"fast",personalization:{warmth:Infinity},owl:{position:{x:NaN,y:4}}}]){
    const p=normalizeUIPrefs(value);
    assert.equal(p.theme,"auto"); assert.equal(p.language,"en"); assert.equal(p.motion,"system");
    assert.equal(p.personalization.warmth,0.5);
  }
});
test("bounded personalization values clamp and strings are capped",()=>{
  const p=normalizeUIPrefs({personalization:{warmth:-2,concision:3,enthusiasm:.4,emoji:.9,customInstructions:"x".repeat(13000),quickResponses:true,suggestedFollowups:false}});
  assert.equal(p.personalization.warmth,0); assert.equal(p.personalization.concision,1);
  assert.equal(p.personalization.customInstructions.length,12000);
  assert.equal(p.personalization.quickResponses,true); assert.equal(p.personalization.suggestedFollowups,false);
});
test("theme and motion obey explicit setting before system",()=>{
  assert.equal(effectiveTheme("light",true),"light"); assert.equal(effectiveTheme("dark",false),"dark"); assert.equal(effectiveTheme("auto",true),"dark");
  const base=normalizeUIPrefs(null);
  assert.equal(effectiveReducedMotion(base,true),true);
  assert.equal(effectiveReducedMotion({...base,motion:"full"},true),false);
  assert.equal(effectiveReducedMotion({...base,motion:"reduced"},false),true);
});
test("persistence canonicalizes and handles denied storage",()=>{
  let saved=""; const storage={setItem(_k,v){saved=v;},getItem(){return saved;}};
  assert.equal(writeUIPrefs({theme:"dark",language:"es"},storage),true);
  assert.equal(readUIPrefs(storage).theme,"dark"); assert.equal(readUIPrefs(storage).language,"es");
  assert.equal(writeUIPrefs(UI_DEFAULTS,{setItem(){throw Error("denied");}}),false);
  assert.deepEqual(readUIPrefs({getItem(){throw Error("denied");}}),normalizeUIPrefs(null));
});
test("owl normalized coordinates round-trip and clamp",()=>{
  const p={x:.27,y:.81}, px=viewportPoint(p,390,844,58), back=normalizeViewportPoint(px.x,px.y,390,844,58);
  assert.ok(Math.abs(back.x-p.x)<1e-9); assert.ok(Math.abs(back.y-p.y)<1e-9);
  assert.deepEqual(normalizeViewportPoint(-50,9999,390,844,58),{x:0,y:1});
});
test("general, notification and voice states are bounded",()=>{
  const p=normalizeUIPrefs({general:{startRoute:"documents"},notifications:{runComplete:false,errors:false,approvals:false},voice:{enabled:false,rate:9}});
  assert.equal(p.general.startRoute,"documents"); assert.equal(p.notifications.runComplete,false); assert.equal(p.voice.enabled,false); assert.equal(p.voice.rate,2);
});
