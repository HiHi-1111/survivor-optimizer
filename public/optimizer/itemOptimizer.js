function generateGearCandidates(profile){
  var out=[];
  var defs=self.SIO_ITEMS||{};
  var entries=((profile.itemsOptimizer||{}).entries)||{};
  var featureMeta={
    light:{label:'Light AF',path:'e',cap:5},
    void:{label:'Void AF',path:'v',cap:5},
    chaos:{label:'Chaos Fusion',path:'c',cap:10},
    xeno:{label:'Xeno Transmute',path:'x',cap:13}
  };
  Object.keys(defs).forEach(function(name){
    var def=defs[name];
    var entry=entries[name]||{state:'enabled',min:{},max:{}};
    if(entry.state==='disabled')return;

    if(entry.mode==='features'||entry.features){
      Object.keys(featureMeta).forEach(function(key){
        var meta=featureMeta[key];
        var itemFeature=(entry.features||{})[key]||{};
        var cur=Number(itemFeature.current);
        if(!isFinite(cur))cur=Number((entry.min||{})[meta.path])||0;
        var max=Number(itemFeature.max)||meta.cap;
        if(cur>=max)return;
        var gainPerLevel=Number(((def.paths||{})[meta.path]||{}).dmgPerLevel)||0.001;
        var step=cur+1;
        var gain=self.estimateRelativeGain(profile,gainPerLevel);
        var cost=Math.max(1,step);
        out.push({category:'Gear',itemName:name,featureKey:key,featureLabel:meta.label,current:cur,next:step,action:'Upgrade '+name+' '+meta.label+' '+cur+' -> '+step,reason:'Best next SS forge feature from your Equipment setup',estimatedGain:gain,cost:{relicCores:cost},efficiency:gain/cost,recommendationType:'add'});
      });
      return;
    }

    if(entry.mode==='simple'||entry.simple){
      var cur2=Number((entry.simple&&entry.simple.current)||(entry.min&&entry.min.star)||0)||0;
      var max2=Number((entry.simple&&entry.simple.max)||(entry.max&&entry.max.star)||10)||10;
      if(max2<=cur2)return;
      var best=0.001;
      Object.keys(def.paths||{}).forEach(function(p){best=Math.max(best,Number(def.paths[p].dmgPerLevel)||0)});
      var step2=cur2+1;
      var gain2=self.estimateRelativeGain(profile,best);
      var cost2=Math.max(1,step2);
      out.push({category:'Gear',itemName:name,current:cur2,next:step2,action:'Upgrade '+name+' stars '+cur2+' -> '+step2,reason:'Best next gear-star upgrade from your equipped setup',estimatedGain:gain2,cost:{relicCores:cost2},efficiency:gain2/cost2,recommendationType:'add'});
      return;
    }

    Object.keys(def.paths||{}).forEach(function(path){
      var cur3=Number((entry.min||{})[path])||0;
      var max3=Number((entry.max||{})[path])||0;
      if(max3<=cur3)return;
      var step3=cur3+1;
      var gain3=self.estimateRelativeGain(profile,(def.paths[path].dmgPerLevel||0.001));
      var cost3=Math.max(1,step3);
      out.push({category:'Gear',itemName:name,current:cur3,next:step3,action:'Upgrade '+name+' '+cur3+' -> '+step3,reason:'Estimated DPS gain per relic core',estimatedGain:gain3,cost:{relicCores:cost3},efficiency:gain3/cost3,recommendationType:'add'});
    });
  });
  if(out.length===0)out.push({_warning:'No gear upgrade range is enabled. Add your equipment forge feature stars first.'});
  return out;
}
self.generateGearCandidates=generateGearCandidates;
