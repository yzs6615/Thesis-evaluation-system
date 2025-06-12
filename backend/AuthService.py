import os
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import jwt
import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///auth.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'super-secret-key')
db = SQLAlchemy(app)
CORS(app)

# 用户模型
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # admin, expert, author
    external_id = db.Column(db.Integer)  # 关联外部服务的ID

# 创建数据库表
with app.app_context():
    db.create_all()

@app.route('/users', methods=['POST'])
def create_user():
    data = request.json
    
    # 检查用户名是否已存在
    if User.query.filter_by(username=data['username']).first():
        return jsonify({"error": "Username already exists"}), 400
    
    # 创建新用户
    user = User(
        username=data['username'],
        password_hash=generate_password_hash(data['password']),
        role=data['role'],
        external_id=data.get('external_id')
    )
    db.session.add(user)
    db.session.commit()
    
    return jsonify({"message": "User created successfully", "user_id": user.id}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    
    # 查找用户
    user = User.query.filter_by(username=data['username']).first()
    
    # 验证密码
    if not user or not check_password_hash(user.password_hash, data['password']):
        return jsonify({"error": "Invalid username or password"}), 401
    
    # 创建JWT令牌
    token = jwt.encode({
        'user_id': user.id,
        'username': user.username,
        'role': user.role,
        'external_id': user.external_id,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    }, app.config['SECRET_KEY'], algorithm='HS256')
    
    return jsonify({
        "message": "Login successful",
        "token": token,
        "role": user.role
    })

@app.route('/verify', methods=['GET'])
def verify_token():
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"error": "Authorization header missing"}), 401
    
    token = auth_header.split(" ")[1]
    try:
        # 验证令牌
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return jsonify(data)
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Token has expired"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid token"}), 401

@app.route('/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    user = User.query.get_or_404(user_id)
    return jsonify({
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "external_id": user.external_id
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003, debug=True)
