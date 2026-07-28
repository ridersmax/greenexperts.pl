const menuButton=document.querySelector('.mobile-toggle');
const menu=document.querySelector('.menu');
if(menuButton&&menu){
  const menuLabels={
    pl:{open:'Otwórz menu',close:'Zamknij menu'},
    de:{open:'Menü öffnen',close:'Menü schließen'},
    en:{open:'Open menu',close:'Close menu'}
  };
  const labels=menuLabels[document.documentElement.lang]||menuLabels.pl;
  menuButton.addEventListener('click',()=>{
    const isOpen=menu.classList.toggle('open');
    menuButton.setAttribute('aria-expanded',String(isOpen));
    menuButton.setAttribute('aria-label',isOpen?labels.close:labels.open);
  });
  menu.querySelectorAll('a').forEach(link=>link.addEventListener('click',()=>{
    menu.classList.remove('open');
    menuButton.setAttribute('aria-expanded','false');
    menuButton.setAttribute('aria-label',labels.open);
  }));
}

const lightbox=document.querySelector('.lightbox');
if(lightbox){
  const lightboxImage=lightbox.querySelector('img');
  const lightboxCaption=lightbox.querySelector('p');
  const closeButton=lightbox.querySelector('.lightbox-close');
  document.querySelectorAll('.zoom-button').forEach(button=>button.addEventListener('click',()=>{
    const figure=button.closest('figure');
    const source=figure.querySelector('img');
    lightboxImage.src=source.src;
    lightboxImage.alt=source.alt;
    lightboxCaption.textContent=figure.querySelector('figcaption')?.textContent||source.alt;
    lightbox.showModal();
    document.body.classList.add('no-scroll');
  }));
  const closeLightbox=()=>{
    lightbox.close();
    document.body.classList.remove('no-scroll');
  };
  closeButton?.addEventListener('click',closeLightbox);
  lightbox.addEventListener('click',event=>{
    if(event.target===lightbox)closeLightbox();
  });
  lightbox.addEventListener('close',()=>document.body.classList.remove('no-scroll'));
}
