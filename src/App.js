import React, { useState, useEffect } from 'react';
import axios from 'axios';

const API_GATEWAY_URL = 'http://your-api-gateway-url';

function App() {
  const [user, setUser] = useState(null);
  const [papers, setPapers] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (token) {
      axios.get(`${API_GATEWAY_URL}/verify`, {
        headers: {
          Authorization: `Bearer ${token}`
        }
      })
      .then(response => {
        setUser(response.data);
        if (response.data.role === 'expert') {
          fetchExpertPapers(response.data.external_id);
        } else if (response.data.role === 'admin') {
          fetchAllPapers();
        }
      })
      .catch(error => {
        console.error('验证失败:', error);
        localStorage.removeItem('token');
      });
    } else {
      setLoading(false);
    }
  }, []);

  const fetchExpertPapers = (expertId) => {
    axios.get(`${API_GATEWAY_URL}/experts/${expertId}/papers`)
      .then(response => {
        setPapers(response.data.papers);
        setLoading(false);
      })
      .catch(error => {
        console.error('获取专家论文失败:', error);
        setLoading(false);
      });
  };

  const fetchAllPapers = () => {
    axios.get(`${API_GATEWAY_URL}/papers`)
      .then(response => {
        setPapers(response.data.papers);
        setLoading(false);
      })
      .catch(error => {
        console.error('获取所有论文失败:', error);
        setLoading(false);
      });
  };

  const handleLogin = (username, password) => {
    axios.post(`${API_GATEWAY_URL}/login`, {
      username,
      password
    })
    .then(response => {
      localStorage.setItem('token', response.data.token);
      setUser(response.data);
      if (response.data.role === 'expert') {
        fetchExpertPapers(response.data.external_id);
      } else if (response.data.role === 'admin') {
        fetchAllPapers();
      }
    })
    .catch(error => {
      console.error('登录失败:', error);
    });
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    setUser(null);
    setPapers([]);
  };

  const handleSubmitScore = (paperId, score, comment) => {
    const token = localStorage.getItem('token');
    axios.post(`${API_GATEWAY_URL}/papers/${paperId}/scores`, {
      score,
      comment
    }, {
      headers: {
        Authorization: `Bearer ${token}`
      }
    })
    .then(response => {
      console.log('评分提交成功:', response.data);
      if (user.role === 'expert') {
        fetchExpertPapers(user.external_id);
      } else if (user.role === 'admin') {
        fetchAllPapers();
      }
    })
    .catch(error => {
      console.error('评分提交失败:', error);
    });
  };

  if (loading) {
    return <div>加载中...</div>;
  }

  if (!user) {
    return (
      <div>
        <h1>登录</h1>
        <input type="text" placeholder="用户名" id="username" />
        <input type="password" placeholder="密码" id="password" />
        <button onClick={() => {
          const username = document.getElementById('username').value;
          const password = document.getElementById('password').value;
          handleLogin(username, password);
        }}>登录</button>
      </div>
    );
  }

  return (
    <div>
      <h1>论文评定系统</h1>
      <button onClick={handleLogout}>退出登录</button>
      <h2>论文列表</h2>
      <ul>
        {papers.map(paper => (
          <li key={paper.id}>
            <p>标题: {paper.title}</p>
            <p>状态: {paper.status}</p>
            <p>平均分: {paper.average_score}</p>
            {user.role === 'expert' && (
              <div>
                <input type="number" placeholder="评分" id={`score-${paper.id}`} />
                <input type="text" placeholder="评论" id={`comment-${paper.id}`} />
                <button onClick={() => {
                  const score = parseInt(document.getElementById(`score-${paper.id}`).value);
                  const comment = document.getElementById(`comment-${paper.id}`).value;
                  handleSubmitScore(paper.id, score, comment);
                }}>提交评分</button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default App;