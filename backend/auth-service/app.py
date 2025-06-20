import os
import json
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy.sql import func
import pika
import jwt
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///auth.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_secret_key')
app.config['JWT_EXPIRATION_DELTA'] = int(os.environ.get('JWT_EXPIRATION_DELTA', 86400))  # 默认1天
db = SQLAlchemy(app)
CORS(app)

# 用户模型
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # admin, author, expert
    external_id = db.Column(db.Integer)  # 关联到其他服务的ID
    created_at = db.Column(db.DateTime, default=func.now())
    last_login = db.Column(db.DateTime)

# 创建数据库表
with app.app_context():
    db.create_all()
    
    # 检查是否已存在管理员账户，如果不存在则创建
    admin = User.query.filter_by(role='admin').first()
    if not admin:
        admin_user = User(
            username='admin',
            password_hash=generate_password_hash('admin123'),
            role='admin'
        )
        db.session.add(admin_user)
        db.session.commit()
        print("已创建默认管理员账户: admin/admin123")

# 连接RabbitMQ
def connect_to_rabbitmq():
    try:
        rabbitmq_url = os.environ.get('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/')
        connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
        channel = connection.channel()
        
        # 声明交换机和队列
        channel.exchange_declare(exchange='thesis_events', exchange_type='topic', durable=True)
        channel.queue_declare(queue='auth_events', durable=True)
        
        # 绑定队列到交换机
        channel.queue_bind(exchange='thesis_events', queue='auth_events', routing_key='auth.#')
        
        return connection, channel
    except Exception as e:
        print(f"RabbitMQ连接失败: {e}")
        return None, None

# 发送消息到RabbitMQ
def publish_message(routing_key, message):
    try:
        _, channel = connect_to_rabbitmq()
        if channel:
            channel.basic_publish(
                exchange='thesis_events',
                routing_key=routing_key,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # 持久化消息
                    content_type='application/json'
                )
            )
            print(f"消息已发送: {routing_key} - {message}")
            return True
        return False
    except Exception as e:
        print(f"发送消息失败: {e}")
        return False

# 生成JWT令牌
def generate_token(user):
    payload = {
        'user_id': user.id,
        'username': user.username,
        'role': user.role,
        'external_id': user.external_id,
        'exp': datetime.utcnow() + timedelta(seconds=app.config['JWT_EXPIRATION_DELTA'])
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

# 验证JWT令牌
def verify_token(token):
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return payload, None
    except jwt.ExpiredSignatureError:
        return None, "令牌已过期"
    except jwt.InvalidTokenError:
        return None, "无效的令牌"

# 创建用户
@app.route('/users', methods=['POST'])
def create_user():
    data = request.json
    
    # 检查必要字段
    required_fields = ['username', 'password', 'role']
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"缺少必要字段: {field}"}), 400
    
    # 检查用户名是否已存在
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"error": "用户名已存在"}), 400
    
    # 检查角色是否有效
    valid_roles = ['admin', 'author', 'expert']
    if data['role'] not in valid_roles:
        return jsonify({"error": f"无效的角色，有效角色为: {', '.join(valid_roles)}"}), 400
    
    # 创建用户
    user = User(
        username=data['username'],
        password_hash=generate_password_hash(data['password']),
        role=data['role'],
        external_id=data.get('external_id')
    )
    
    db.session.add(user)
    db.session.commit()
    
    # 发布用户创建事件
    publish_message('auth.user_created', {
        'user_id': user.id,
        'username': user.username,
        'role': user.role,
        'external_id': user.external_id
    })
    
    return jsonify({
        "message": "用户创建成功",
        "user_id": user.id,
        "username": user.username,
        "role": user.role
    }), 201

# 用户登录
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    
    # 检查必要字段
    if 'username' not in data or 'password' not in data:
        return jsonify({"error": "请提供用户名和密码"}), 400
    
    # 查找用户
    user = User.query.filter_by(username=data['username']).first()
    
    # 验证密码
    if not user or not check_password_hash(user.password_hash, data['password']):
        return jsonify({"error": "用户名或密码错误"}), 401
    
    # 更新最后登录时间
    user.last_login = datetime.now()
    db.session.commit()
    
    # 生成令牌
    token = generate_token(user)
    
    # 发布登录事件
    publish_message('auth.user_login', {
        'user_id': user.id,
        'username': user.username,
        'role': user.role,
        'timestamp': datetime.now().isoformat()
    })
    
    # 获取用户详细信息
    user_details = {}
    try:
        if user.role == 'author':
            service_url = os.environ.get('AUTHOR_SERVICE_URL', 'http://author-service:5005')
            response = requests.get(f"{service_url}/authors/{user.external_id}")
            if response.status_code == 200:
                user_details = response.json()
        elif user.role == 'expert':
            service_url = os.environ.get('EXPERT_SERVICE_URL', 'http://expert-service:5001')
            response = requests.get(f"{service_url}/experts/{user.external_id}")
            if response.status_code == 200:
                user_details = response.json()
    except Exception as e:
        print(f"获取用户详情失败: {e}")
    
    return jsonify({
        "message": "登录成功",
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "external_id": user.external_id,
            **user_details
        }
    })

# 验证令牌
@app.route('/verify', methods=['GET'])
def verify():
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "缺少认证令牌"}), 401
    
    token = auth_header.split(' ')[1]
    payload, error = verify_token(token)
    
    if error:
        return jsonify({"error": error}), 401
    
    return jsonify(payload)

# 获取用户信息
@app.route('/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    # 验证请求者身份
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "缺少认证令牌"}), 401
    
    token = auth_header.split(' ')[1]
    payload, error = verify_token(token)
    
    if error:
        return jsonify({"error": error}), 401
    
    # 只有管理员或用户本人可以查看用户信息
    if payload['role'] != 'admin' and payload['user_id'] != user_id:
        return jsonify({"error": "无权限查看此用户信息"}), 403
    
    user = User.query.get_or_404(user_id)
    
    return jsonify({
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "external_id": user.external_id,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None
    })

# 更新用户信息
@app.route('/users/<int:user_id>', methods=['PUT'])
def update_user(user_id):
    # 验证请求者身份
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "缺少认证令牌"}), 401
    
    token = auth_header.split(' ')[1]
    payload, error = verify_token(token)
    
    if error:
        return jsonify({"error": error}), 401
    
    # 只有管理员或用户本人可以更新用户信息
    if payload['role'] != 'admin' and payload['user_id'] != user_id:
        return jsonify({"error": "无权限更新此用户信息"}), 403
    
    user = User.query.get_or_404(user_id)
    data = request.json
    
    # 更新密码
    if 'password' in data:
        user.password_hash = generate_password_hash(data['password'])
    
    # 只有管理员可以更新角色
    if 'role' in data and payload['role'] == 'admin':
        valid_roles = ['admin', 'author', 'expert']
        if data['role'] not in valid_roles:
            return jsonify({"error": f"无效的角色，有效角色为: {', '.join(valid_roles)}"}), 400
        user.role = data['role']
    
    # 只有管理员可以更新外部ID
    if 'external_id' in data and payload['role'] == 'admin':
        user.external_id = data['external_id']
    
    db.session.commit()
    
    return jsonify({
        "message": "用户信息更新成功",
        "user_id": user.id
    })

# 获取用户列表（仅管理员）
@app.route('/users', methods=['GET'])
def get_users():
    # 验证请求者身份
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "缺少认证令牌"}), 401
    
    token = auth_header.split(' ')[1]
    payload, error = verify_token(token)
    
    if error:
        return jsonify({"error": error}), 401
    
    # 只有管理员可以查看用户列表
    if payload['role'] != 'admin':
        return jsonify({"error": "无权限查看用户列表"}), 403
    
    # 支持按角色筛选
    role = request.args.get('role')
    
    query = User.query
    
    if role:
        query = query.filter_by(role=role)
    
    users = query.all()
    result = []
    
    for user in users:
        result.append({
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "external_id": user.external_id,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None
        })
    
    return jsonify({"users": result})

# 获取认证统计数据
@app.route('/statistics', methods=['GET'])
def get_statistics():
    # 验证请求者身份
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "缺少认证令牌"}), 401
    
    token = auth_header.split(' ')[1]
    payload, error = verify_token(token)
    
    if error:
        return jsonify({"error": error}), 401
    
    # 只有管理员可以查看统计数据
    if payload['role'] != 'admin':
        return jsonify({"error": "无权限查看统计数据"}), 403
    
    # 统计用户总数
    total_users = User.query.count()
    
    # 统计各角色用户数量
    role_counts = db.session.query(
        User.role,
        func.count(User.id).label('count')
    ).group_by(User.role).all()
    
    # 统计最近7天注册用户数
    seven_days_ago = datetime.now() - timedelta(days=7)
    recent_users = User.query.filter(User.created_at >= seven_days_ago).count()
    
    # 统计最近7天登录用户数
    recent_logins = User.query.filter(User.last_login >= seven_days_ago).count()
    
    # 转换为前端友好的格式
    role_stats = {role: count for role, count in role_counts}
    
    return jsonify({
        "total_users": total_users,
        "role_statistics": role_stats,
        "recent_registrations": recent_users,
        "recent_logins": recent_logins
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003, debug=True)