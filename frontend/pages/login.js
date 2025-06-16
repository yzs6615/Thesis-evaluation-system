document.getElementById('login-form').addEventListener('submit', function(event) {
  event.preventDefault();
  // 获取输入的用户名和密码
  const username = document.getElementById('username').value;
  const password = document.getElementById('password').value;

  // 验证用户名和密码
  if (username === 'root' && password === '123456') {
    // 验证通过，隐藏登录页面，显示主应用界面
    document.getElementById('login-page').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
  } else {
    // 验证失败，弹出提示框
    alert('用户名或密码错误，请重试！');
  }
});