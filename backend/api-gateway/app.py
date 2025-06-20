import os
import requests
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import json

app = Flask(__name__)
CORS(app)

# 服务地址配置
SERVICE_URLS = {
    'paper': os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000'),
    'expert': os.environ.get('EXPERT_SERVICE_URL', 'http://expert-service:5001'),
    'score': os.environ.get('SCORE_SERVICE_URL', 'http://score-service:5002'),
    'auth': os.environ.get('AUTH_SERVICE_URL', 'http://auth-service:5003'),
    'notification': os.environ.get('NOTIFICATION_SERVICE_URL', 'http://notification-service:5004')
}

# 需要认证的路径
AUTH_REQUIRED_PATHS = [
    '/api/papers',
    '/api/experts',
    '/api/scores',
    '/api/users',
    '/api/notifications'
]

# 公开路径（不需要认证）
PUBLIC_PATHS = [
    '/api/auth/login',
    '/api/auth/register'
]

# 验证令牌
def verify_token(token):
    try:
        response = requests.get(
            f"{SERVICE_URLS['auth']}/verify",
            headers={"Authorization": f"Bearer {token}"}
        )
        return response.status_code == 200, response.json() if response.status_code == 200 else None
    except Exception as e:
        print(f"验证令牌失败: {e}")
        return False, None

# 请求转发
def forward_request(service, path, method, headers=None, json_data=None, params=None):
    if headers is None:
        headers = {}
    
    url = f"{SERVICE_URLS[service]}{path}"
    
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=json_data,
            params=params
        )
        
        return Response(
            response.content,
            status=response.status_code,
            content_type=response.headers.get('Content-Type', 'application/json')
        )
    except Exception as e:
        print(f"转发请求失败: {e}")
        return jsonify({"error": f"服务不可用: {str(e)}"}), 503

# 认证中间件
@app.before_request
def auth_middleware():
    # 检查是否需要认证
    path = request.path
    
    # 公开路径不需要认证
    if any(path.startswith(public_path) for public_path in PUBLIC_PATHS):
        return None
    
    # 检查是否需要认证
    if any(path.startswith(auth_path) for auth_path in AUTH_REQUIRED_PATHS):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "需要认证令牌"}), 401
        
        token = auth_header.split(' ')[1]
        is_valid, payload = verify_token(token)
        
        if not is_valid:
            return jsonify({"error": "无效的认证令牌"}), 403
    
    return None

# 认证路由
@app.route('/api/auth/login', methods=['POST'])
def login():
    return forward_request('auth', '/login', 'POST', json_data=request.json)

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    role = data.get('role')
    
    # 根据角色调用不同的服务
    if role == 'expert':
        # 先创建专家记录
        expert_response = forward_request('expert', '/experts', 'POST', json_data=data)
        
        if expert_response.status_code != 201:
            return expert_response
        
        # 专家创建成功，返回结果
        return expert_response
    elif role == 'author':
        # 创建作者记录（假设有作者服务）
        # 这里简化处理，直接创建认证账户
        return forward_request('auth', '/users', 'POST', json_data=data)
    else:
        # 其他角色直接创建认证账户
        return forward_request('auth', '/users', 'POST', json_data=data)

# 论文服务路由
@app.route('/api/papers', methods=['GET', 'POST'])
def papers():
    return forward_request('paper', '/papers', request.method, 
                          headers=request.headers, 
                          json_data=request.json if request.is_json else None,
                          params=request.args)

@app.route('/api/papers/<int:paper_id>', methods=['GET', 'PUT', 'DELETE'])
def paper(paper_id):
    return forward_request('paper', f'/papers/{paper_id}', request.method, 
                          headers=request.headers, 
                          json_data=request.json if request.is_json else None)

@app.route('/api/papers/<int:paper_id>/status', methods=['GET', 'PUT'])
def paper_status(paper_id):
    return forward_request('paper', f'/papers/{paper_id}/status', request.method, 
                          headers=request.headers, 
                          json_data=request.json if request.is_json else None)

@app.route('/api/papers/<int:paper_id>/scores', methods=['GET', 'POST'])
def paper_scores(paper_id):
    return forward_request('score', f'/papers/{paper_id}/scores', request.method, 
                          headers=request.headers, 
                          json_data=request.json if request.is_json else None)

@app.route('/api/papers/<int:paper_id>/average', methods=['GET'])
def paper_average_score(paper_id):
    return forward_request('score', f'/papers/{paper_id}/average', 'GET', 
                          headers=request.headers)

# 专家服务路由
@app.route('/api/experts', methods=['GET', 'POST'])
def experts():
    return forward_request('expert', '/experts', request.method, 
                          headers=request.headers, 
                          json_data=request.json if request.is_json else None,
                          params=request.args)

@app.route('/api/experts/available', methods=['GET'])
def available_experts():
    return forward_request('expert', '/experts/available', 'GET', 
                          headers=request.headers, 
                          params=request.args)

@app.route('/api/experts/<int:expert_id>', methods=['GET', 'PUT'])
def expert(expert_id):
    return forward_request('expert', f'/experts/{expert_id}', request.method, 
                          headers=request.headers, 
                          json_data=request.json if request.is_json else None)

@app.route('/api/experts/<int:expert_id>/papers', methods=['GET'])
def expert_papers(expert_id):
    return forward_request('expert', f'/experts/{expert_id}/papers', 'GET', 
                          headers=request.headers, 
                          params=request.args)

# 用户服务路由
@app.route('/api/users', methods=['GET'])
def users():
    return forward_request('auth', '/users', 'GET', 
                          headers=request.headers, 
                          params=request.args)

@app.route('/api/users/<int:user_id>', methods=['GET', 'PUT'])
def user(user_id):
    return forward_request('auth', f'/users/{user_id}', request.method, 
                          headers=request.headers, 
                          json_data=request.json if request.is_json else None)

# 通知服务路由
@app.route('/api/users/<int:user_id>/notifications', methods=['GET'])
def user_notifications(user_id):
    return forward_request('notification', f'/users/{user_id}/notifications', 'GET', 
                          headers=request.headers, 
                          params=request.args)

@app.route('/api/users/<int:user_id>/notifications/unread-count', methods=['GET'])
def user_notifications_unread_count(user_id):
    return forward_request('notification', f'/users/{user_id}/notifications/unread-count', 'GET', 
                          headers=request.headers, 
                          params=request.args)

@app.route('/api/users/<int:user_id>/notifications/read-all', methods=['PUT'])
def user_notifications_read_all(user_id):
    return forward_request('notification', f'/users/{user_id}/notifications/read-all', 'PUT', 
                          headers=request.headers, 
                          params=request.args)

@app.route('/api/notifications/<int:notification_id>/read', methods=['PUT'])
def mark_notification_read(notification_id):
    return forward_request('notification', f'/notifications/{notification_id}/read', 'PUT', 
                          headers=request.headers)

@app.route('/api/notifications/<int:notification_id>', methods=['DELETE'])
def delete_notification(notification_id):
    return forward_request('notification', f'/notifications/{notification_id}', 'DELETE', 
                          headers=request.headers)

# 统计数据路由
@app.route('/api/statistics/papers', methods=['GET'])
def paper_statistics():
    return forward_request('paper', '/statistics', 'GET', 
                          headers=request.headers)

@app.route('/api/statistics/experts', methods=['GET'])
def expert_statistics():
    return forward_request('expert', '/statistics', 'GET', 
                          headers=request.headers)

@app.route('/api/statistics/scores', methods=['GET'])
def score_statistics():
    return forward_request('score', '/statistics', 'GET', 
                          headers=request.headers)

@app.route('/api/statistics/auth', methods=['GET'])
def auth_statistics():
    return forward_request('auth', '/statistics', 'GET', 
                          headers=request.headers)

@app.route('/api/statistics/notifications', methods=['GET'])
def notification_statistics():
    return forward_request('notification', '/statistics', 'GET', 
                          headers=request.headers)

# 健康检查
@app.route('/health', methods=['GET'])
def health_check():
    services_status = {}
    
    for service_name, service_url in SERVICE_URLS.items():
        try:
            response = requests.get(f"{service_url}/health", timeout=2)
            services_status[service_name] = {
                "status": "up" if response.status_code == 200 else "down",
                "code": response.status_code
            }
        except Exception as e:
            services_status[service_name] = {
                "status": "down",
                "error": str(e)
            }
    
    all_up = all(status["status"] == "up" for status in services_status.values())
    
    return jsonify({
        "status": "healthy" if all_up else "degraded",
        "services": services_status
    }), 200 if all_up else 503

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5006, debug=True)