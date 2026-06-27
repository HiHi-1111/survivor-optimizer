window.SO={};
SO.$=s=>document.querySelector(s);SO.$$=s=>[...document.querySelectorAll(s)];SO.STORE='survivorOptimizerProfileV6';
SO.state={scenario:'LME Expedition Phase',order:'default',setMode:false,gearMeta:{}};
SO.data={
 scenarios:['LME Expedition Phase','LME Battle Phase',"Ender's Echo"],
 gear:{Weapon:['SS Starforged Havoc','Lightchaser','Void Power','Sword of Disorder'],Necklace:['SS Judgement Necklace','Eternal Necklace','Voidwaker Emblem','Chaos Necklace'],Gloves:['SS Gloves','Eternal Gloves','Voidwaker Handguards','Chaos Gauntlets'],Armor:['SS Armor','Eternal Suit','Voidwaker Windbreaker','Armor of Quietus'],Belt:['SS Belt','Eternal Belt','Voidwaker Sash','Twisting Belt'],Boots:['SS Boots','Eternal Boots','Voidwaker Treads','Shoes of Confusion']},
 systems:['S Grade','SS','Astral Forge','Cosmic Cast','Xeno','Relic Core','Wishlist','Selectors','Designs','Merge/Fodder','Salvage'],
 tech:['Drone Mode','Drill Shot Mode','Durian Mode','Lightning Mode','Guardian Mode','Brick Mode','Molotov','Rocket','Drone','Drill'],
 skills:['Drone','Drill Shot','Durian','Molotov','Rocket','Energy Cube','HP Bullet','Exo Bracer','Ammo Thruster','HE Fuel'],
 survivors:['Yang','Metallia','King','Worm','Catnips','Common','Tsukuyomi','Wesson','Yelena','Spongebob','Squidward','Raphael','Leonardo','Donatello','April','Splinter','Patrick','Sandy','Squid Guard','Arcade Hero','Void Cadet','Cyber Medic','Green Ranger','Crimson Agent','Gary','Other A','Other B','Other C'],
 featured:['Raphael','Robot Core','Aqua Scout'],sets:['Custom Collection Set #1','Custom Collection Set #2','Custom Collection Set #3','Custom Collection Set #4'],
 collects:Array.from({length:100},(_,i)=>`Collectible ${i+1}`),evo:['Expose Weakness','Viva la Materia','Overreaction','Watchmaker'],lunar:['ATK','Shield','Cart','Chest','Burst','Crit','Tower','Crystal'],pets:['Main pet: Rex','Motivation','Inspiration','Encouragement','Battle Lust','Gary'],mounts:['Electric Scooter','Tech Hoverboard','Doomsteed']
};
SO.mark=(label)=>String(label).split(/\s+/).map(x=>x[0]).join('').slice(0,3).toUpperCase();
SO.img=(c,k,l,b='↯')=>`<div class='${c} icon-block' data-kind='${k}'><span class='art-mark'>${SO.mark(l)}</span>${b?`<span class='icon-badge'>${b}</span>`:''}</div>`;
SO.starRows=()=>`<div class='forge-rows'><div><b>★</b><span>Yellow</span><em>3/6</em></div><div><b class='red-star'>★</b><span>Red</span><em>0/6</em></div><div><b>AF</b><span>Astral</span><em>0</em></div><div><b>SS</b><span>Forge</span><em>0</em></div><div><b>RC</b><span>Relic</span><em>0</em></div></div>`;
SO.stars=(n,t=6)=>`<span class='stars'>${Array.from({length:t},(_,i)=>`<span class='${i<n?'star-on':'star-off'}'>★</span>`).join('')}</span>`;
SO.opts=(a,s)=>a.map(x=>`<option ${x===s?'selected':''}>${x}</option>`).join('');
SO.toast=t=>{document.querySelector('.toast')?.remove();let d=document.createElement('div');d.className='toast';d.textContent=t;document.body.appendChild(d);setTimeout(()=>d.remove(),1700)};
SO.modal=(title,body,onSave)=>{let d=document.createElement('div');d.className='modal-backdrop';d.innerHTML=`<div class='modal-card'><h3>${title}</h3>${body}<div class='modal-actions'><button class='mini-btn' data-close>Cancel</button><button class='mini-btn' data-save>Save</button></div></div>`;document.body.appendChild(d);d.querySelector('[data-close]').onclick=()=>d.remove();d.querySelector('[data-save]').onclick=()=>{onSave?.(d);d.remove();SO.calc?.();SO.save?.()};d.onclick=e=>{if(e.target===d)d.remove()}};
SO.edit=(title,fields,saveText)=>SO.modal(title,`<div class='editor-grid'>${fields.map(f=>`<label>${f[0]}${f[2]?`<select id='${f[1]}'>${f[2].map(x=>`<option>${x}</option>`).join('')}</select>`:`<input id='${f[1]}' type='${f[3]||'number'}' value='${f[4]||0}'>`}</label>`).join('')}</div>`,()=>SO.toast(saveText||`${title} saved`));