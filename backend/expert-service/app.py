import os
import json
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy.sql import func
import pika

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///experts.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
CORS(app)

# 专家模型
class Expert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    expertise = db.Column(db.String(200))  # 专业领域，用于匹配论文
    institution = db.Column(db.String(200))  # 所属机构
    title = db.Column(db.String(50))  # 职称
    bio = db.Column(db.Text)  # 简介
    max_papers = db.Column(db.Integer, default=10)  # 最大评审论文数
    created_at = db.Column(db.DateTime, default=func.now())

# 创建数据库表
with app.app_context():
    db.create_all()

# 连接RabbitMQ
def connect_to_rabbitmq():
    try:
        rabbitmq_url = os.environ.get('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/')
        connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
        channel = connection.channel()
        
        # 声明交换机和队列
        channel.exchange_declare(exchange='thesis_events', exchange_type='topic', durable=True)
        channel.queue_declare(queue='expert_events', durable=True)
        
        # 绑定队列到交换机
        channel.queue_bind(exchange='thesis_events', queue='expert_events', routing_key='expert.#')
        
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

# 注册专家
@app.route('/experts', methods=['POST'])
def register_expert():
    data = request.json
    
    # 检查邮箱是否已存在
    if Expert.query.filter_by(email=data['email']).first():
        return jsonify({"error": "邮箱已被注册"}), 400
    
    # 创建专家记录
    expert = Expert(
        name=data['name'],
        email=data['email'],
        expertise=data.get('expertise', ''),
        institution=data.get('institution', ''),
        title=data.get('title', ''),
        bio=data.get('bio', ''),
        max_papers=data.get('max_papers', 10)
    )
    db.session.add(expert)
    db.session.commit()
    
    # 调用认证服务创建账户
    try:
        auth_service_url = os.environ.get('AUTH_SERVICE_URL', 'http://auth-service:5003')
        auth_response = requests.post(
            f"{auth_service_url}/users",
            json={
                "username": data['email'],
                "password": data['password'],
                "role": "expert",
                "external_id": expert.id
            }
        )
        if auth_response.status_code != 201:
            # 如果认证服务失败，回滚专家注册
            db.session.delete(expert)
            db.session.commit()
            return jsonify({"error": "创建认证账户失败"}), 500
    except Exception as e:
        # 如果认证服务失败，回滚专家注册
        db.session.delete(expert)
        db.session.commit()
        return jsonify({"error": f"认证服务错误: {str(e)}"}), 500
    
    # 发布专家注册事件
    publish_message('expert.registered', {
        'expert_id': expert.id,
        'name': expert.name,
        'email': expert.email
    })
    
    return jsonify({
        "message": "专家注册成功",
        "expert_id": expert.id,
        "name": expert.name
    }), 201

# 获取专家详情
@app.route('/experts/<int:expert_id>', methods=['GET'])
def get_expert(expert_id):
    expert = Expert.query.get_or_404(expert_id)
    
    # 获取专家当前的论文数量
    paper_count = 0
    try:
        paper_service_url = os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000')
        counts_response = requests.get(f"{paper_service_url}/experts/paper-counts")
        if counts_response.status_code == 200:
            counts = counts_response.json().get('counts', [])
            for count in counts:
                if count['expert_id'] == expert_id:
                    paper_count = count['paper_count']
                    break
    except Exception as e:
        print(f"获取论文数量失败: {e}")
    
    return jsonify({
        "id": expert.id,
        "name": expert.name,
        "email": expert.email,
        "expertise": expert.expertise,
        "institution": expert.institution,
        "title": expert.title,
        "bio": expert.bio,
        "max_papers": expert.max_papers,
        "current_papers": paper_count,
        "created_at": expert.created_at.isoformat() if expert.created_at else None
    })

# 获取专家列表
@app.route('/experts', methods=['GET'])
def get_experts():
    # 支持按专业领域筛选
    expertise = request.args.get('expertise')
    
    query = Expert.query
    
    if expertise:
        query = query.filter(Expert.expertise.like(f'%{expertise}%'))
    
    experts = query.all()
    result = []
    
    for expert in experts:
        result.append({
            "id": expert.id,
            "name": expert.name,
            "email": expert.email,
            "expertise": expert.expertise,
            "institution": expert.institution,
            "title": expert.title
        })
    
    return jsonify({"experts": result})

# 获取可用专家
@app.route('/experts/available', methods=['GET'])
def get_available_experts():
    min_papers = int(request.args.get('min_papers', 0))
    max_papers = int(request.args.get('max_papers', 10))
    expertise = request.args.get('expertise')
    
    # 获取所有专家
    query = Expert.query
    
    if expertise:
        query = query.filter(Expert.expertise.like(f'%{expertise}%'))
    
    experts = query.all()
    
    # 获取每个专家当前分配的论文数量
    paper_counts = {}
    try:
        paper_service_url = os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000')
        counts_response = requests.get(f"{paper_service_url}/experts/paper-counts")
        if counts_response.status_code == 200:
            for count in counts_response.json().get('counts', []):
                paper_counts[count['expert_id']] = count['paper_count']
    except Exception as e:
        print(f"获取论文分配数量失败: {e}")
    
    # 筛选可用专家
    available_experts = []
    for expert in experts:
        count = paper_counts.get(expert.id, 0)
        if min_papers <= count <= max_papers:
            available_experts.append({
                "id": expert.id,
                "name": expert.name,
                "email": expert.email,
                "expertise": expert.expertise,
                "institution": expert.institution,
                "title": expert.title,
                "current_papers": count,
                "max_papers": expert.max_papers
            })
    
    return jsonify({"experts": available_experts})

# 更新专家信息
@app.route('/experts/<int:expert_id>', methods=['PUT'])
def update_expert(expert_id):
    expert = Expert.query.get_or_404(expert_id)
    data = request.json
    
    # 更新专家信息
    if 'name' in data:
        expert.name = data['name']
    
    if 'expertise' in data:
        expert.expertise = data['expertise']
    
    if 'institution' in data:
        expert.institution = data['institution']
    
    if 'title' in data:
        expert.title = data['title']
    
    if 'bio' in data:
        expert.bio = data['bio']
    
    if 'max_papers' in data:
        expert.max_papers = data['max_papers']
    
    db.session.commit()
    
    return jsonify({
        "message": "专家信息更新成功",
        "expert_id": expert.id
    })

# 获取专家的论文
@app.route('/experts/<int:expert_id>/papers', methods=['GET'])
def get_expert_papers(expert_id):
    # 验证专家是否存在
    expert = Expert.query.get_or_404(expert_id)
    
    try:
        # 调用论文服务获取专家的论文
        paper_service_url = os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000')
        status = request.args.get('status')
        
        url = f"{paper_service_url}/experts/{expert_id}/papers"
        if status:
            url += f"?status={status}"
        
        papers_response = requests.get(url)
        
        if papers_response.status_code == 200:
            return papers_response.json()
        else:
            return jsonify({"error": "获取论文失败"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 获取专家统计数据
@app.route('/statistics', methods=['GET'])
def get_statistics():
    # 统计专家总数
    total_experts = Expert.query.count()
    
    # 统计各机构专家数量
    institution_counts = db.session.query(
        Expert.institution,
        func.count(Expert.id).label('count')
    ).group_by(Expert.institution).all()
    
    # 统计各职称专家数量
    title_counts = db.session.query(
        Expert.title,
        func.count(Expert.id).label('count')
    ).group_by(Expert.title).all()
    
    # 转换为前端友好的格式
    institution_stats = {institution: count for institution, count in institution_counts if institution}
    title_stats = {title: count for title, count in title_counts if title}
    
    return jsonify({
        "total_experts": total_experts,
        "institution_statistics": institution_stats,
        "title_statistics": title_stats
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)