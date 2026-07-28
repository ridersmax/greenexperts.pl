(()=>{
  const supported={
    pl:{name:'Polski',label:'Język'},
    de:{name:'Deutsch',label:'Sprache'},
    en:{name:'English',label:'Language'}
  };
  const language=(document.documentElement.lang||'pl').slice(0,2).toLowerCase();
  const current=supported[language]?language:'pl';
  const parts=location.pathname.split('/').filter(Boolean);
  if(parts[0]==='pl'||parts[0]==='de'||parts[0]==='en')parts.shift();
  const page=parts.join('/')||'index.html';
  const pathFor=code=>{
    if(page==='index.html')return code==='pl'?'/':`/${code}/`;
    return code==='pl'?`/${page}`:`/${code}/${page}`;
  };
  const host=document.querySelector('.footer .wrap')||document.querySelector('.content .wrap')||document.body;
  const wrap=document.createElement('div');
  wrap.className='language-switcher-wrap'+(host.closest('.footer')?'':' language-switcher-light');
  const switcher=document.createElement('div');
  switcher.className='language-switcher';
  switcher.innerHTML=`
    <button class="language-switcher-button" type="button" aria-expanded="false" aria-haspopup="menu">
      ${supported[current].label}: ${supported[current].name}
    </button>
    <div class="language-switcher-menu" role="menu">
      ${Object.entries(supported).map(([code,item])=>`
        <a role="menuitem" lang="${code}" hreflang="${code}" href="${pathFor(code)}"${code===current?' aria-current="true"':''}>${item.name}</a>
      `).join('')}
    </div>`;
  wrap.append(switcher);
  host.append(wrap);
  const button=switcher.querySelector('button');
  const menu=switcher.querySelector('.language-switcher-menu');
  const close=()=>{
    menu.classList.remove('open');
    button.setAttribute('aria-expanded','false');
  };
  button.addEventListener('click',()=>{
    const open=menu.classList.toggle('open');
    button.setAttribute('aria-expanded',String(open));
  });
  document.addEventListener('click',event=>{
    if(!switcher.contains(event.target))close();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'){
      close();
      button.focus();
    }
  });
})();
