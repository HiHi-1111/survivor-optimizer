(()=>{
  const $=(s,r=document)=>r.querySelector(s);
  const $$=(s,r=document)=>Array.from(r.querySelectorAll(s));

  function fire(input){
    input.dispatchEvent(new Event('input',{bubbles:true}));
    input.dispatchEvent(new Event('change',{bubbles:true}));
  }

  function dots(input,max,label){
    const wrap=document.createElement('div');
    wrap.className='click-level-wrap';
    wrap.innerHTML='<div class="click-level-label">'+label+'</div>';
    const row=document.createElement('div');
    row.className='click-level-row';
    const get=()=>Math.max(0,Math.min(max,parseInt(input.value||'0',10)||0));
    const redraw=()=>{
      const v=get();
      row.innerHTML='';
      for(let i=0;i<=max;i++){
        const b=document.createElement('button');
        b.type='button';
        b.className='level-dot'+(i<=v&&i>0?' filled':'')+(i===v?' current':'');
        b.textContent=i===0?'0':'★';
        b.title=label+' '+i;
        b.onclick=()=>{input.value=i;fire(input);redraw();syncSiblings(input)};
        row.appendChild(b);
      }
    };
    wrap.appendChild(row);
    input.addEventListener('change',redraw);
    redraw();
    return wrap;
  }

  function syncSiblings(input){
    const row=input.closest('.path-row');
    if(!row)return;
    row.querySelectorAll('.click-level-wrap').forEach(w=>w.remove());
    enhancePathRow(row,true);
  }

  function enhancePathRow(row,force=false){
    if(row.dataset.enhanced==='1'&&!force)return;
    row.dataset.enhanced='1';
    row.classList.add('enhanced');
    const inputs=$$('input',row);
    if(inputs.length<2)return;
    const key=row.querySelector('.path-key')?.textContent?.trim()?.toLowerCase()||'';
    const max=key==='x'?13:key==='base'?3:10;
    row.appendChild(dots(inputs[0],max,'current'));
    row.appendChild(dots(inputs[1],max,'target'));
  }

  function enhanceItemOptimizer(){
    const grid=$('#itemOptimizerGrid');
    if(!grid)return;
    if(!grid.dataset.help){
      grid.dataset.help='1';
      const help=document.createElement('div');
      help.className='upgrade-help';
      help.textContent='Tap stars to set current and target forge levels. Left side is current, right side is target. Optimize uses the gap between them.';
      grid.parentElement?.insertBefore(help,grid);
    }
    $$('.path-row',grid).forEach(r=>enhancePathRow(r));
  }

  function enhanceLevelItem(item){
    if(item.dataset.enhanced==='1')return;
    const input=$('input',item);
    if(!input)return;
    item.dataset.enhanced='1';
    item.classList.add('enhanced');
    const max=parseInt(input.max||'10',10)||10;
    const box=document.createElement('div');
    box.className='quick-level-box';
    box.appendChild(dots(input,max,'level'));
    item.appendChild(box);
  }

  function enhanceLevels(){
    ['#techGrid','#petsGrid','#mountsGrid'].forEach(sel=>$$(sel+' .level-item').forEach(enhanceLevelItem));
  }

  function run(){enhanceItemOptimizer();enhanceLevels();}
  document.addEventListener('DOMContentLoaded',()=>{run();setTimeout(run,150);setTimeout(run,600)});
  document.addEventListener('click',e=>{
    if(e.target.closest('.nav-item')||e.target.closest('#btnLoadSample')||e.target.closest('#btnImport')){
      setTimeout(run,80);setTimeout(run,300);
    }
  });
  new MutationObserver(()=>run()).observe(document.documentElement,{childList:true,subtree:true});
})();
