import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeNetValue } from '../src/net-value.mjs';

const scope = { itemId:'synthetic-item',sailingId:'synthetic-sailing',category:'suite',variant:'basic',occupancy:2,promotionScope:'none' };
function observation(value, patch={}) {
  return {id:'synthetic',observedAt:'2026-01-01T00:00:00Z',status:'valid',stale:false,availability:'available',
    value,currency:'USD',priceBasis:'total',taxesFees:'included',sourceClass:'synthetic',scope:{...scope},...patch};
}
const credit=(id,amount,patch={})=>({id,amount,currency:'USD',kind:'credit',sourceClass:'synthetic',...patch});
function check(baselinePrice,currentPrice,patch={}) {
  return analyzeNetValue({baseline:observation(baselinePrice,{id:'baseline'}),current:observation(currentPrice,{id:'current'}),
    baselineAdjustments:[],currentAdjustments:[],termsCompared:true,unknownValueComponents:[],
    materialTermDifferences:[],...patch});
}
test('lower headline 100 gives known delta 100 with complete explicit evidence',()=>{
  const r=check(500,400);assert.deepEqual([r.status,r.headlineDelta,r.knownNetDelta,r.direction],['complete',100,100,'better']);
});
test('losing known 75 credit reduces 100 price saving to 25',()=>{
  const r=check(500,400,{baselineAdjustments:[credit('obc',75)]});assert.deepEqual([r.knownValueDelta,r.knownNetDelta],[-75,25]);
});
test('losing known 150 credit makes lower price worse',()=>{
  const r=check(500,400,{baselineAdjustments:[credit('obc',150)]});assert.deepEqual([r.knownNetDelta,r.direction],[-50,'worse']);
});
test('price rise 20 with quantified inclusion 100 gives net 80',()=>{
  const r=check(500,520,{currentAdjustments:[credit('inclusion',100,{kind:'inclusion'})]});assert.deepEqual([r.headlineDelta,r.knownNetDelta],[-20,80]);
});
test('same price can gain, lose, or preserve value',()=>{
  assert.equal(check(500,500,{currentAdjustments:[credit('a',20)]}).knownNetDelta,20);
  assert.equal(check(500,500,{baselineAdjustments:[credit('a',20)]}).knownNetDelta,-20);
  assert.equal(check(500,500).direction,'same');
});
test('unknown deposit/refund term is partial even with known positive delta',()=>{
  const r=check(500,400,{materialTermDifferences:['refundability']});assert.deepEqual([r.status,r.knownNetDelta,r.direction],['partial',100,'unknown']);
  assert.deepEqual(r.unknownComponents,['term:refundability']);
});
test('guarantee and bundle changes prevent silently complete claim',()=>{
  assert.deepEqual(check(500,400,{materialTermDifferences:['guarantee','bundle']}).unknownComponents,['term:bundle','term:guarantee']);
});
test('unverified terms block complete claim',()=>{
  const r=check(500,400,{termsCompared:false});assert.equal(r.status,'partial');assert.deepEqual(r.unknownComponents,['terms-not-verified']);
});
test('unknown known value components are deterministic and partial',()=>{
  const r=check(500,400,{unknownValueComponents:['perk','deposit','perk']});
  assert.deepEqual(r.unknownComponents,['deposit','perk']);assert.equal(r.direction,'unknown');
});
for(const key of ['currency','priceBasis','taxesFees','sourceClass']){
  test(`different ${key} is non-comparable`,()=>{
    const r=check(500,400,{current:observation(400,{[key]:'different'})});
    assert.equal(r.status,'non-comparable');assert.equal(r.knownNetDelta,null);
  });
}
test('different occupancy is non-comparable',()=>{
  assert.equal(check(500,400,{current:observation(400,{scope:{...scope,occupancy:4}})}).status,'non-comparable');
});
test('stale/failed latest and missing price produce insufficient evidence',()=>{
  assert.equal(check(500,400,{current:observation(400,{stale:true})}).status,'insufficient-evidence');
  assert.equal(check(500,400,{current:observation(null,{status:'failed',availability:'unknown'})}).status,'insufficient-evidence');
});
test('zero and negative signed adjustments are stable',()=>{
  assert.equal(check(500,400,{currentAdjustments:[credit('zero',0)]}).knownNetDelta,100);
  assert.equal(check(500,400,{currentAdjustments:[credit('penalty',-25,{kind:'adjustment'})]}).knownNetDelta,75);
});
test('order independent whole-cent sums avoid decimal drift',()=>{
  const a=[credit('a',.1),credit('b',.2)];
  assert.equal(check(100,99.9,{currentAdjustments:a}).knownNetDelta,.4);
  assert.equal(check(100,99.9,{currentAdjustments:a.toReversed()}).knownNetDelta,.4);
});
test('duplicate adjustment identifiers reject with same or conflicting value',()=>{
  assert.throws(()=>check(500,400,{currentAdjustments:[credit('x',10),credit('x',10)]}),/duplicate adjustment/);
  assert.throws(()=>check(500,400,{currentAdjustments:[credit('x',10),credit('x',11)]}),/duplicate adjustment/);
});
test('same identifier across both options can represent changed credit',()=>{
  assert.equal(check(500,400,{currentAdjustments:[credit('obc',25)],baselineAdjustments:[credit('obc',100)]}).knownNetDelta,25);
});
test('invalid or sub-cent amounts, mismatch currency, missing provenance reject',()=>{
  for(const amount of [NaN,Infinity,'20',1.005]){
    assert.throws(()=>check(500,400,{currentAdjustments:[credit('bad',amount)]}),TypeError);
  }
  assert.throws(()=>check(500,400,{current:observation(-1)}),TypeError);
  assert.throws(()=>check(500,400,{currentAdjustments:[credit('bad',10,{currency:'EUR'})]}),/currency/);
  assert.throws(()=>check(500,400,{currentAdjustments:[credit('bad',10,{sourceClass:''})]}),/sourceClass/);
});
test('coverage must be supplied, cannot silently imply zero value',()=>{
  assert.throws(()=>check(500,400,{currentAdjustments:undefined}),/array/);
  assert.throws(()=>check(500,400,{termsCompared:undefined}),/boolean/);
  assert.throws(()=>analyzeNetValue({current:observation(400),baseline:observation(500),
    currentAdjustments:[],baselineAdjustments:[],termsCompared:true}),/supplied explicitly/);
});
test('frozen output has no personal eligibility claim',()=>{
  const r=check(500,400);
  assert.ok(Object.isFrozen(r)&&Object.isFrozen(r.unknownComponents));
  assert.equal('eligible' in r,false);
  assert.match(r.manualNextStep,/verify personalized eligibility/i);
});
