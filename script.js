const burger = document.querySelector('.burger');
const nav = document.querySelector('.nav');

if (burger && nav) {
  burger.addEventListener('click', () => {
    const isOpen = nav.classList.toggle('open');
    burger.setAttribute('aria-expanded', String(isOpen));
    document.body.classList.toggle('menu-open', isOpen);
  });

  nav.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => {
      nav.classList.remove('open');
      burger.setAttribute('aria-expanded', 'false');
      document.body.classList.remove('menu-open');
    });
  });
}

const observer = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.12 });

document.querySelectorAll('.reveal').forEach((el) => observer.observe(el));


const buyPackageButtons = document.querySelectorAll('.buy-package');

buyPackageButtons.forEach((button) => {
  button.addEventListener('click', async () => {
    const packageName = button.dataset.package || 'выбранный пакет';
    const message = `Здравствуйте. Хочу купить ${packageName}`;

    try {
      await navigator.clipboard.writeText(message);
    } catch (error) {
      // Если браузер не разрешил копирование, ссылка всё равно откроет Telegram.
    }
  });
});
