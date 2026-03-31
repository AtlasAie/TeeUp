// ===== NAV SCROLL =====
const nav = document.getElementById('nav');
if (nav) {
  window.addEventListener('scroll', () => {
    nav.classList.toggle('scrolled', window.scrollY > 10);
  }, { passive: true });
}

// ===== USER DROPDOWN =====
function toggleUserMenu() {
  const d = document.getElementById('userDropdown');
  if (d) d.classList.toggle('open');
}

document.addEventListener('click', (e) => {
  const menu = document.getElementById('userMenu');
  const dropdown = document.getElementById('userDropdown');
  if (menu && dropdown && !menu.contains(e.target)) {
    dropdown.classList.remove('open');
  }
});

// ===== MOBILE MENU =====
function toggleMobile() {
  const menu = document.getElementById('mobileMenu');
  const overlay = document.getElementById('mobileOverlay');
  const hamburger = document.getElementById('hamburger');
  if (!menu) return;
  const open = menu.classList.toggle('open');
  overlay.classList.toggle('open', open);
  document.body.style.overflow = open ? 'hidden' : '';
  if (hamburger) {
    const spans = hamburger.querySelectorAll('span');
    if (open) {
      spans[0] && (spans[0].style.transform = 'rotate(45deg) translate(5px,5px)');
      spans[1] && (spans[1].style.opacity = '0');
      spans[2] && (spans[2].style.transform = 'rotate(-45deg) translate(5px,-5px)');
    } else {
      spans.forEach(s => { s.style.transform = ''; s.style.opacity = ''; });
    }
  }
}

// ===== FLASH AUTO-DISMISS =====
document.querySelectorAll('.flash').forEach(el => {
  setTimeout(() => el.remove(), 5000);
});
