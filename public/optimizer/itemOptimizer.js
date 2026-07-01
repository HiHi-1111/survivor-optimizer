function generateGearCandidates(profile){
  var out=[];
  var defs=self.SIO_ITEMS||{};
  var entries=((profile.itemsOptimizer||{}).entries)||{};
  var featureMeta={light:{label:'Light AF',path:'e',cap:5},void:{label:'Void AF',path:'v',cap:5},chaos:{label:'Chaos Fusion',path:'c',cap:10},xeno:{label:'Xeno Transmute',path:'x',cap:13}};
  Object.keys(defs).forEach(function(name){
    var def=defs[name];
    var entry=entries[name]||{state:'enabled',min:{},max:{}};
    if(entry.state==='disabled')return;
    var isSS=!!def.ss||def.rarity==='ss';
    if(isSS&&(entry.mode==='features'||entry.features)){
      Object.keys(featureMeta).forEach(function(key){
        var meta=featureMeta[key];
        var itemFeature=(entry.features||{})[key]||{};
        var cur=Number(itemFeature.current); if(!isFinite(cur))cur=Number((entry.min||{})[meta.path])||0;
        var max=Number(itemFeature.max)||meta.cap;
        if(cur>=max)return;
        var gainPerLevel=Number(((def.paths||{})[meta.path]||{}).dmgPerLevel)||0.001;
        var step=cur+1,gain=self.estimateRelativeGain(profile,gainPerLevel),cost=Math.max(1,step);
        out.push({category:'Gear',itemName:name,featureKey:key,featureLabel:meta.label,current:cur,next:step,action:'Upgrade '+name+' '+meta.label+' '+cur+' -> '+step,reason:'SS item feature upgrade',estimatedGain:gain,cost:{relicCores:cost},efficiency:gain/cost,recommendationType:'add'});
      });
      return;
    }
    if(!isSS&&(entry.mode==='standard'||entry.standard||entry.simple)){
      var cur2=Number((entry.standard&&entry.standard.current)||(entry.simple&&entry.simple.current)||(entry.min&&entry.min.star)||0)||0;
      var max2=Number((entry.standard&&entry.standard.max)||(entry.simple&&entry.simple.max)||(entry.max&&entry.max.star)||3)||3;
      if(cur2>=max2)return;
      var best=Number((((def.paths||{}).star)||{}).dmgPerLevel)||0.004;
      var step2=cur2+1,gain2=self.estimateRelativeGain(profile,best),cost2=Math.max(1,step2);
      out.push({category:'Gear',itemName:name,featureKey:'star',featureLabel:'Astral Forge',current:cur2,next:step2,action:'Upgrade '+name+' Astral Forge '+cur2+' -> '+step2,reason:'Normal/S gear AF upgrade. SS-only paths are hidden for this item.',estimatedGain:gain2,cost:{cores:cost2},efficiency:gain2/cost2,recommendationType:'add'});
      return;
    }
  });
  if(out.length===0)out.push({_warning:'No gear upgrade range is enabled. Add equipment stars first.'});
  return out;
}
self.generateGearCandidates=generateGearCandidates;
