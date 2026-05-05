// EasyObs Documentation - sidebar active link highlighting
(function() {
  const links = document.querySelectorAll('.docs-nav-link');
  const current = window.location.pathname.split('/').pop() || 'index.html';
  links.forEach(link => {
    const href = link.getAttribute('href');
    if (href === current) {
      link.classList.add('active');
    } else {
      link.classList.remove('active');
    }
  });
})();
