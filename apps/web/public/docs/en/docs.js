// EasyObs Documentation - sidebar active link highlighting & language switch
(function() {
  // Active link highlighting
  var links = document.querySelectorAll('.docs-nav-link');
  var current = window.location.pathname.split('/').pop() || 'index.html';
  links.forEach(function(link) {
    var href = link.getAttribute('href');
    if (href === current) {
      link.classList.add('active');
    } else {
      link.classList.remove('active');
    }
  });

  // Language switcher
  var STORAGE_KEY = 'easyobs.uiLocale';
  var pathParts = window.location.pathname.split('/');
  var docsIdx = pathParts.indexOf('docs');

  var currentLang = 'en';
  if (docsIdx >= 0 && pathParts.length > docsIdx + 1) {
    var langSegment = pathParts[docsIdx + 1];
    if (langSegment === 'en' || langSegment === 'kr') {
      currentLang = langSegment;
    }
  }

  try { localStorage.setItem(STORAGE_KEY, currentLang === 'kr' ? 'ko' : 'en'); } catch(e) {}

  function switchLang(targetLang) {
    var page = current;
    var basePath = window.location.pathname.substring(0, window.location.pathname.indexOf('/docs/') + '/docs/'.length);
    window.location.href = basePath + targetLang + '/' + page;
  }

  var header = document.querySelector('.docs-header');
  if (header) {
    var switcher = document.createElement('div');
    switcher.className = 'docs-lang-switcher';
    switcher.innerHTML =
      '<button class="docs-lang-btn' + (currentLang === 'en' ? ' active' : '') + '" data-lang="en">EN</button>' +
      '<button class="docs-lang-btn' + (currentLang === 'kr' ? ' active' : '') + '" data-lang="kr">KR</button>';
    header.appendChild(switcher);

    switcher.addEventListener('click', function(e) {
      var btn = e.target.closest('.docs-lang-btn');
      if (btn) {
        var lang = btn.getAttribute('data-lang');
        if (lang !== currentLang) {
          switchLang(lang);
        }
      }
    });
  }
})();
