// 密码显示切换
const regTogglePassword = document.getElementById('reg-toggle-password');
const regPassword = document.getElementById('reg-password');
const regToggleConfirmPassword = document.getElementById('reg-toggle-confirm-password');
const regConfirmPassword = document.getElementById('reg-confirm-password');

regTogglePassword.addEventListener('click', () => {
  const type = regPassword.getAttribute('type') === 'password' ? 'text' : 'password';
  regPassword.setAttribute('type', type);
  regTogglePassword.querySelector('i').classList.toggle('fa-eye-slash');
  regTogglePassword.querySelector('i').classList.toggle('fa-eye');
});

regToggleConfirmPassword.addEventListener('click', () => {
  const type = regConfirmPassword.getAttribute('type') === 'password' ? 'text' : 'password';
  regConfirmPassword.setAttribute('type', type);
  regToggleConfirmPassword.querySelector('i').classList.toggle('fa-eye-slash');
  regToggleConfirmPassword.querySelector('i').classList.toggle('fa-eye');
});

// 侧边栏展开/收缩
const sidebarToggle = document.getElementById('sidebar-toggle');
const sidebar = document.getElementById('sidebar');

sidebarToggle.addEventListener('click', () => {
  sidebar.classList.toggle('-translate-x-full');
});

// 侧边栏分组展开/收缩
const sidebarGroupHeaders = document.querySelectorAll('.sidebar-group-header');
sidebarGroupHeaders.forEach(header => {
  header.addEventListener('click', () => {
    const content = header.nextElementSibling;
    content.classList.toggle('hidden');
    header.querySelector('i').classList.toggle('fa-caret-down');
    header.querySelector('i').classList.toggle('fa-caret-up');
  });
});

// 用户菜单显示/隐藏
const userMenuBtn = document.getElementById('user-menu-btn');
const userMenu = document.getElementById('user-menu');

userMenuBtn.addEventListener('click', () => {
  userMenu.classList.toggle('hidden');
});

// 点击页面其他地方隐藏用户菜单
document.addEventListener('click', (event) => {
  if (!userMenuBtn.contains(event.target) && !userMenu.contains(event.target)) {
    userMenu.classList.add('hidden');
  }
});