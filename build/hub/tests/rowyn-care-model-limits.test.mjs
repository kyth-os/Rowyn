import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parsePowerSnapshot, healthPercent, storageUsagePercent, parseStorageSnapshot } from '../src/services/rowynCareModel.ts';

test('battery percentages permit measured fractions while rejecting nonfinite values', () => {
  const data={state:'ok',batteries:[{capacity:52.5,status:'Discharging',healthPercent:83.125}],chargeLimit:{state:'available',value:80},profile:{state:'available',value:'balanced'},capabilities:{state:'available',value:['mem']}};
  assert.equal(parsePowerSnapshot(data).batteries[0].healthPercent,83.125);
  assert.equal(parsePowerSnapshot(data).batteries[0].capacity,52.5);
  for (const value of [NaN,Infinity,-1,201]) assert.throws(()=>parsePowerSnapshot({...data,batteries:[{...data.batteries[0],healthPercent:value}]}));
});

test('ratios return unknown for invalid inputs instead of fabricated clamps', () => {
  assert.equal(healthPercent(Infinity,100),null);
  assert.equal(healthPercent(400,100),null);
  assert.equal(healthPercent(105,100),105);
  assert.equal(storageUsagePercent(Infinity,Infinity),null);
  assert.equal(storageUsagePercent(0,0),null);
});

test('frontend inventory cap matches the native bounded thousand-row contract', () => {
 const entries=Array.from({length:1000},(_,i)=>({reference:`org.example.App${i}/x86_64/stable`,name:'Example',scope:'user',installedSize:'10 MB'}));
 assert.equal(parseStorageSnapshot({state:'ok',volumes:[],inventory:[{category:'flatpakApps',status:'available',count:1000,estimatedBytes:null,entries}]}).inventory[0].entries.length,1000);
});

 test('available power capabilities require a bounded array of supported tokens', () => {
  const data={state:'ok',batteries:[],chargeLimit:{state:'unsupported',value:null},profile:{state:'unsupported',value:null},capabilities:{state:'available',value:['mem']}};
  assert.deepEqual(parsePowerSnapshot(data).capabilities,{state:'available',value:['mem']});
  for (const value of [null,undefined,'mem',{},Array(17).fill('mem'),['mystery']]) {
   const malformed={...data,capabilities:{state:'available',...(value===undefined?{}:{value})}};
   assert.throws(()=>parsePowerSnapshot(malformed),`accepted ${String(value)}`);
  }
  assert.deepEqual(parsePowerSnapshot({...data,capabilities:{state:'unsupported',value:null}}).capabilities,{state:'unsupported',value:[]});
 });
