import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { digestMatchedNotes, parseRecoverySnapshot } from "../src/services/rowynRecoveryModel.ts";
const a = `sha256:${"a".repeat(64)}`, b = `sha256:${"b".repeat(64)}`;
test("reads actual bootc nested image identity, version and timestamp", () => {
 const parsed = parseRecoverySnapshot({ status: { booted:{image:{image:{image:{reference:"registry/os",digest:a},version:"1.2"}},timestamp:"2026-10-01"}, staged:{image:{image:{reference:"registry/new",digest:b}}}, rollback:{image:{image:"registry/old",imageDigest:a}} } });
 assert.deepEqual(parsed, {booted:{image:"registry/os",version:"1.2",digest:a,timestamp:"2026-10-01"},staged:{image:"registry/new",version:null,digest:b,timestamp:null},rollback:{image:"registry/old",version:null,digest:a,timestamp:null}});
});
test("missing slots differ from malformed reported values; malformed root unavailable", () => {
 assert.deepEqual(parseRecoverySnapshot({status:{}}),{booted:null,staged:null,rollback:null});
 for(const invalid of [null,[],"bad",{}, {status:null},{status:"oops"}]) assert.equal(parseRecoverySnapshot(invalid),null);
 assert.deepEqual(parseRecoverySnapshot({status:{booted:{},staged:{image:{reference:7}},rollback:[]}}),{booted:{image:null,version:null,digest:null,timestamp:null},staged:{image:null,version:null,digest:null,timestamp:null},rollback:{image:null,version:null,digest:null,timestamp:null}});
});
test("timestamps inside image metadata remain visible", () => {
 assert.equal(parseRecoverySnapshot({status:{booted:{image:{image:{image:'registry/os'},timestamp:'2026-11-04T00:00:00Z'}}}}).booted.timestamp,'2026-11-04T00:00:00Z');
});
test("binds notes only to an exact full digest in real history-entry records", () => {
 const entry={version:"1.2",flavor:"stable",release_url:"https://example.invalid/release",published_at:"2026-10-01",digest:a,highlights_title:"Release",highlights:["one"],notes_intro:"Intro",package_total:12};
 assert.deepEqual(digestMatchedNotes(a,[{...entry,digest:b},entry]),{matched:true,title:"Release",highlights:["one"],version:"1.2",published_at:"2026-10-01"});
 for(const [requested,history] of [[a,[{...entry,digest:b}]],[a,[]],[a,null],[a,[{...entry,highlights:"bad"}]],[a.toUpperCase(),[entry]],["sha256:abc",[entry]],[a,{...entry,digest:a}]]) assert.equal(digestMatchedNotes(requested,history).matched,false);
});
test("bundled named component export renders with React server renderer", async () => {
 const dir = await mkdtemp(join(process.cwd(), "tests/.rowyn-recovery-"));
 try {
  const outfile = join(dir, "component.cjs");
  await build({ entryPoints: [new URL("../src/components/RecoveryClarity.tsx", import.meta.url).pathname], outfile, bundle: true, platform: "node", format: "cjs", packages: "external", jsx: "automatic" });
  const require = createRequire(import.meta.url), component = require(outfile), React = require("react"), server = require("react-dom/server");
  assert.equal(typeof component.RecoveryClarity, "function");
  const html = server.renderToStaticMarkup(React.createElement(component.RecoveryClarity));
  assert.match(html, /href="#\/backups"/);
 assert.match(html, /Refresh cached status/); assert.match(html, /Backups/); assert.match(html, /Show notes for selected deployment/); assert.match(html, /disabled="" class="action-button action-button-primary"/);
 } finally { await rm(dir, { recursive: true, force: true }); }
});
