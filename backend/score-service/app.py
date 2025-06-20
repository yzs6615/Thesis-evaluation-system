import os
import json
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy.sql import func
import pika
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///scores.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
CORS(app)

# 评分模型
class Score(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    paper_id = db.Column(db.Integer, nullable=False)
    expert_id = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Float, nullable=False)  # 总分
    originality = db.Column(db.Float)  # 原创性
    relevance = db.Column(db.Float)  # 相关性
    methodology = db.Column(db.Float)  # 方法论
    clarity = db.Column(db.Float)  # 清晰度
    significance = db.Column(db.Float)  # 重要性
    comments = db.Column(db.Text)  # 评审意见
    created_at = db.Column(db.DateTime, default=func.now())
    updated_at = db.Column(db.DateTime, default=func.now(), onupdate=func.now())
    
    # 确保每篇论文每位专家只有一个评分
    __table_args__ = (db.UniqueConstraint('paper_id', 'expert_id', name='unique_paper_expert'),)

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
        channel.queue_declare(queue='score_events', durable=True)
        
        # 绑定队列到交换机
        channel.queue_bind(exchange='thesis_events', queue='score_events', routing_key='score.#')
        
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

# 验证专家身份和权限
def verify_expert(token, expert_id, paper_id):
    try:
        # 验证令牌
        auth_service_url = os.environ.get('AUTH_SERVICE_URL', 'http://auth-service:5003')
        auth_response = requests.get(
            f"{auth_service_url}/verify",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        if auth_response.status_code != 200:
            return False, "无效的认证令牌"
        
        auth_data = auth_response.json()
        
        # 检查用户角色和ID
        if auth_data.get('role') != 'expert' or auth_data.get('external_id') != expert_id:
            return False, "无权限执行此操作"
        
        # 检查专家是否被分配了该论文
        paper_service_url = os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000')
        assignment_response = requests.get(
            f"{paper_service_url}/experts/{expert_id}/papers"
        )
        
        if assignment_response.status_code != 200:
            return False, "无法验证论文分配"
        
        papers = assignment_response.json().get('papers', [])
        paper_assigned = any(paper['id'] == paper_id for paper in papers)
        
        if not paper_assigned:
            return False, "该论文未分配给您评审"
        
        return True, ""
    except Exception as e:
        return False, str(e)

# 提交评分
@app.route('/papers/<int:paper_id>/scores', methods=['POST'])
def submit_score(paper_id):
    data = request.json
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    expert_id = data.get('expert_id')
    
    # 验证专家身份和权限
    is_valid, error_message = verify_expert(token, expert_id, paper_id)
    if not is_valid:
        return jsonify({"error": error_message}), 403
    
    # 检查是否已经评分
    existing_score = Score.query.filter_by(paper_id=paper_id, expert_id=expert_id).first()
    
    if existing_score:
        # 更新现有评分
        existing_score.score = data['score']
        existing_score.originality = data.get('originality')
        existing_score.relevance = data.get('relevance')
        existing_score.methodology = data.get('methodology')
        existing_score.clarity = data.get('clarity')
        existing_score.significance = data.get('significance')
        existing_score.comments = data.get('comments', '')
        existing_score.updated_at = datetime.now()
        
        db.session.commit()
        score_id = existing_score.id
        is_new = False
    else:
        # 创建新评分
        score = Score(
            paper_id=paper_id,
            expert_id=expert_id,
            score=data['score'],
            originality=data.get('originality'),
            relevance=data.get('relevance'),
            methodology=data.get('methodology'),
            clarity=data.get('clarity'),
            significance=data.get('significance'),
            comments=data.get('comments', '')
        )
        
        db.session.add(score)
        db.session.commit()
        score_id = score.id
        is_new = True
    
    # 更新论文服务中的分配状态
    try:
        paper_service_url = os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000')
        update_response = requests.put(
            f"{paper_service_url}/papers/{paper_id}/assignments/{expert_id}",
            json={"status": "completed"}
        )
        
        if update_response.status_code != 200:
            print(f"更新分配状态失败: {update_response.text}")
    except Exception as e:
        print(f"调用论文服务失败: {e}")
    
    # 检查论文评审状态
    try:
        check_response = requests.get(f"{paper_service_url}/papers/{paper_id}/check-status")
        if check_response.status_code != 200:
            print(f"检查论文状态失败: {check_response.text}")
    except Exception as e:
        print(f"调用论文状态检查失败: {e}")
    
    # 发布评分事件
    event_type = 'score.updated' if not is_new else 'score.submitted'
    publish_message(event_type, {
        'score_id': score_id,
        'paper_id': paper_id,
        'expert_id': expert_id,
        'score': data['score']
    })
    
    # 计算平均分并通知
    calculate_average_score(paper_id)
    
    return jsonify({
        "message": "评分提交成功",
        "score_id": score_id
    }), 201 if is_new else 200

# 获取论文评分
@app.route('/papers/<int:paper_id>/scores', methods=['GET'])
def get_paper_scores(paper_id):
    # 验证请求者身份（可以是管理员或论文作者）
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    
    if not token:
        return jsonify({"error": "需要认证令牌"}), 401
    
    try:
        # 验证令牌
        auth_service_url = os.environ.get('AUTH_SERVICE_URL', 'http://auth-service:5003')
        auth_response = requests.get(
            f"{auth_service_url}/verify",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        if auth_response.status_code != 200:
            return jsonify({"error": "无效的认证令牌"}), 403
        
        auth_data = auth_response.json()
        role = auth_data.get('role')
        
        # 如果不是管理员，检查是否是论文作者
        if role != 'admin':
            paper_service_url = os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000')
            paper_response = requests.get(f"{paper_service_url}/papers/{paper_id}")
            
            if paper_response.status_code != 200:
                return jsonify({"error": "无法验证论文信息"}), 500
            
            paper_data = paper_response.json()
            
            # 如果不是作者也不是管理员，拒绝访问
            if role == 'author' and paper_data.get('author_id') != auth_data.get('external_id'):
                return jsonify({"error": "无权限查看此论文评分"}), 403
            elif role == 'expert':
                # 专家只能看到自己的评分和平均分
                expert_id = auth_data.get('external_id')
                score = Score.query.filter_by(paper_id=paper_id, expert_id=expert_id).first()
                
                if not score:
                    return jsonify({"error": "您未对此论文进行评分"}), 403
                
                # 计算平均分
                avg_score = db.session.query(func.avg(Score.score)).filter(Score.paper_id == paper_id).scalar() or 0
                
                return jsonify({
                    "paper_id": paper_id,
                    "your_score": {
                        "score": score.score,
                        "originality": score.originality,
                        "relevance": score.relevance,
                        "methodology": score.methodology,
                        "clarity": score.clarity,
                        "significance": score.significance,
                        "comments": score.comments,
                        "submitted_at": score.created_at.isoformat() if score.created_at else None
                    },
                    "average_score": round(avg_score, 2)
                })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    # 获取所有评分
    scores = Score.query.filter_by(paper_id=paper_id).all()
    
    result = []
    for score in scores:
        # 获取专家信息
        expert_name = "未知专家"
        try:
            expert_service_url = os.environ.get('EXPERT_SERVICE_URL', 'http://expert-service:5001')
            expert_response = requests.get(f"{expert_service_url}/experts/{score.expert_id}")
            if expert_response.status_code == 200:
                expert_data = expert_response.json()
                expert_name = expert_data.get('name', "未知专家")
        except Exception as e:
            print(f"获取专家信息失败: {e}")
        
        result.append({
            "id": score.id,
            "expert_id": score.expert_id,
            "expert_name": expert_name,
            "score": score.score,
            "originality": score.originality,
            "relevance": score.relevance,
            "methodology": score.methodology,
            "clarity": score.clarity,
            "significance": score.significance,
            "comments": score.comments,
            "submitted_at": score.created_at.isoformat() if score.created_at else None
        })
    
    # 计算平均分
    avg_score = db.session.query(func.avg(Score.score)).filter(Score.paper_id == paper_id).scalar() or 0
    
    return jsonify({
        "paper_id": paper_id,
        "scores": result,
        "average_score": round(avg_score, 2),
        "total_reviews": len(result)
    })

# 获取论文平均分
@app.route('/papers/<int:paper_id>/average', methods=['GET'])
def get_average_score(paper_id):
    # 计算平均分
    avg_score = db.session.query(func.avg(Score.score)).filter(Score.paper_id == paper_id).scalar() or 0
    count = Score.query.filter_by(paper_id=paper_id).count()
    
    return jsonify({
        "paper_id": paper_id,
        "average_score": round(avg_score, 2),
        "total_reviews": count
    })

# 计算平均分并通知
def calculate_average_score(paper_id):
    # 计算平均分
    avg_score = db.session.query(func.avg(Score.score)).filter(Score.paper_id == paper_id).scalar() or 0
    count = Score.query.filter_by(paper_id=paper_id).count()
    
    # 如果所有专家都已评分，通知论文服务和作者
    try:
        paper_service_url = os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000')
        paper_response = requests.get(f"{paper_service_url}/papers/{paper_id}")
        
        if paper_response.status_code == 200:
            paper_data = paper_response.json()
            required_reviews = paper_data.get('required_reviews', 3)
            
            if count >= required_reviews:
                # 更新论文状态为已评审
                update_response = requests.put(
                    f"{paper_service_url}/papers/{paper_id}/status",
                    json={"status": "reviewed", "average_score": round(avg_score, 2)}
                )
                
                if update_response.status_code == 200:
                    # 发送通知给作者
                    author_id = paper_data.get('author_id')
                    paper_title = paper_data.get('title', "未知论文")
                    
                    notification_service_url = os.environ.get('NOTIFICATION_SERVICE_URL', 'http://notification-service:5004')
                    requests.post(
                        f"{notification_service_url}/notifications",
                        json={
                            "user_id": author_id,
                            "type": "paper_reviewed",
                            "title": "论文评审完成",
                            "message": f"您的论文《{paper_title}》已完成评审，平均分为{round(avg_score, 2)}分。",
                            "data": {
                                "paper_id": paper_id,
                                "average_score": round(avg_score, 2)
                            }
                        }
                    )
                    
                    # 发布论文评审完成事件
                    publish_message('score.paper_reviewed', {
                        'paper_id': paper_id,
                        'average_score': round(avg_score, 2),
                        'total_reviews': count
                    })
    except Exception as e:
        print(f"计算平均分和通知失败: {e}")

# 获取评分统计数据
@app.route('/statistics', methods=['GET'])
def get_statistics():
    # 统计总评分数
    total_scores = Score.query.count()
    
    # 统计平均分分布
    score_ranges = [
        {'range': '0-60', 'count': 0},
        {'range': '60-70', 'count': 0},
        {'range': '70-80', 'count': 0},
        {'range': '80-90', 'count': 0},
        {'range': '90-100', 'count': 0}
    ]
    
    scores = Score.query.with_entities(Score.score).all()
    for (score,) in scores:
        if score < 60:
            score_ranges[0]['count'] += 1
        elif score < 70:
            score_ranges[1]['count'] += 1
        elif score < 80:
            score_ranges[2]['count'] += 1
        elif score < 90:
            score_ranges[3]['count'] += 1
        else:
            score_ranges[4]['count'] += 1
    
    # 统计各维度平均分
    dimensions = {
        'originality': db.session.query(func.avg(Score.originality)).scalar() or 0,
        'relevance': db.session.query(func.avg(Score.relevance)).scalar() or 0,
        'methodology': db.session.query(func.avg(Score.methodology)).scalar() or 0,
        'clarity': db.session.query(func.avg(Score.clarity)).scalar() or 0,
        'significance': db.session.query(func.avg(Score.significance)).scalar() or 0
    }
    
    # 统计总平均分
    overall_average = db.session.query(func.avg(Score.score)).scalar() or 0
    
    return jsonify({
        "total_scores": total_scores,
        "score_distribution": score_ranges,
        "dimension_averages": {k: round(v, 2) for k, v in dimensions.items()},
        "overall_average": round(overall_average, 2)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)