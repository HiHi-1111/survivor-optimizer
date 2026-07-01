(function(){
  var g=typeof self!=='undefined'?self:window;
  function pending(id){return{maxStars:10,dmgPerStar:0,costCurve:function(s){return Math.max(1,(s+1)*8)},sourceStatus:'exact_name_pending',namePending:true,iconId:id}}
  g.SIO_COLLECTIBLES={};
  for(var i=1;i<=72;i++){
    var id='Collectible_IconID_'+String(i).padStart(3,'0');
    g.SIO_COLLECTIBLES[id]=pending(id);
  }
})();
