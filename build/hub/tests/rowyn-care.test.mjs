import test from 'node:test';
import assert from 'node:assert/strict';
import { parseStorageSnapshot, parsePowerSnapshot, storageUsagePercent, healthPercent } from '../src/services/rowynCareModel.ts';

test('storage parser validates safe byte counts, bounded usage, and per-app inventory',()=>{
 const s=parseStorageSnapshot({state:'partial',volumes:[{name:'System',freeBytes:20,totalBytes:100}],inventory:[{category:'applications',count:1,estimatedBytes:null,status:'available',entries:[{reference:'org.demo.App',name:'Demo',scope:'user',installedSize:'1.2 GB'}]}]});
 assert.equal(s.volumes[0].usagePercent,80);assert.equal(s.inventory[0].entries[0].reference,'org.demo.App');assert.equal(s.inventory[0].entries[0].installedSize,'1.2 GB');
 assert.equal(storageUsagePercent(null,100),null);assert.equal(storageUsagePercent(101,100),null);assert.equal(storageUsagePercent(1,0),null);assert.equal(storageUsagePercent(Number.MAX_SAFE_INTEGER+1,100),null);assert.ok(storageUsagePercent(0,100)<=100);
});
test('storage parser rejects malformed entries and unknown measures remain null',()=>{
 const s=parseStorageSnapshot({state:'unavailable',volumes:[{name:'/',freeBytes:null,totalBytes:null}],inventory:[{category:'app',count:null,estimatedBytes:'NaN',status:'unavailable'}]});assert.equal(s.inventory[0].estimatedBytes,null);assert.equal(s.volumes[0].usagePercent,null);
 assert.throws(()=>parseStorageSnapshot({state:'ok',volumes:'bad',inventory:[]}));assert.throws(()=>parseStorageSnapshot({state:'ok',volumes:[],inventory:[{category:'x'}]}));assert.throws(()=>parseStorageSnapshot({state:'ok',volumes:[],inventory:[{category:'x',status:'available',entries:[{reference:'x'}]}]}));
});
test('power parser preserves unknown states and enforces percentage ranges',()=>{
 const p=parsePowerSnapshot({state:'partial',batteries:[],chargeLimit:{state:'unsupported',value:null},profile:{state:'unavailable',value:null},capabilities:{state:'unsupported',value:[]}});assert.equal(p.chargeLimit.state,'unsupported');assert.equal(healthPercent(40,80),50);assert.equal(healthPercent(0,80),null);assert.equal(healthPercent(90,80),112.5);assert.equal(healthPercent(300,80),null);
 assert.throws(()=>parsePowerSnapshot({state:'ok',batteries:[{capacity:101}]}));assert.throws(()=>parsePowerSnapshot({state:'ok',batteries:[{capacity:Infinity}]}));assert.throws(()=>parsePowerSnapshot({state:'ok',batteries:[],chargeLimit:{state:'available',value:101},profile:{state:'available',value:'balanced'},capabilities:{state:'available',value:[]}}));
});
