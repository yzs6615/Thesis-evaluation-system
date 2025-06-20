import os
import json
import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy.sql import func
import pika
import threading
import time

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///papers.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
CORS(app)

# 论文模型
class Paper(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    author_id = db.Column(db.Integer, nullable=False)  # 作者ID
    author_name = db.Column(db.String(100), nullable=False)  # 作者姓名
    abstract = db.Column(db.Text)
    keywords = db.Column(db.String(200))  # 关键词，用于匹配专家
    file_path = db.Column(db.String(200))  # 论文文件路径
    status = db.Column(db.String(50), default='submitted')  # submitted, assigned, in_review, reviewed
    submission_date = db.Column(db.DateTime, default=func.now())
    average_score = db.Column(db.Float, default=None)
    review_completed_date = db.Column(db.DateTime, default=None)

# 专家-论文分配模型
class ExpertPaperAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    expert_id = db.Column(db.Integer, nullable=False)
    expert_name = db.Column(db.String(100))  # 冗余存储专家姓名，方便查询
    paper_id = db.Column(db.Integer, nullable=False)
    paper_title = db.Column(db.String(200))  # 冗余存储论文标题，方便查询
    status = db.Column(db.String(50), default='assigned')  # assigned, in_progress, completed
    score = db.Column(db.Integer, default=None)  # 评分（0-100）
    comment = db.Column(db.Text)  # 评审意见
    assigned_date = db.Column(db.DateTime, default=func.now())
    completed_date = db.Column(db.DateTime, default=None)

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
        channel.queue_declare(queue='paper_assignments', durable=True)
        channel.queue_declare(queue='paper_reviews', durable=True)
        channel.queue_declare(queue='notifications', durable=True)
        
        # 绑定队列到交换机
        channel.queue_bind(exchange='thesis_events', queue='paper_assignments', routing_key='paper.assignment')
        channel.queue_bind(exchange='thesis_events', queue='paper_reviews', routing_key='paper.review')
        channel.queue_bind(exchange='thesis_events', queue='notifications', routing_key='notification.#')
        
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

# 提交论文接口
@app.route('/papers', methods=['POST'])
def submit_paper():
    data = request.json
    
    # 验证用户身份
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"error": "Authorization header missing"}), 401
    
    try:
        # 调用认证服务验证令牌
        auth_service_url = os.environ.get('AUTH_SERVICE_URL', 'http://auth-service:5003')
        auth_response = requests.get(
            f"{auth_service_url}/verify",
            headers={"Authorization": auth_header}
        )
        if auth_response.status_code != 200:
            return jsonify({"error": "Invalid token"}), 401
        
        user_data = auth_response.json()
        author_id = user_data.get('external_id')
        author_name = user_data.get('username')
        
        # 创建论文记录
        paper = Paper(
            title=data['title'],
            author_id=author_id,
            author_name=author_name,
            abstract=data.get('abstract', ''),
            keywords=data.get('keywords', ''),
            file_path=data.get('file_path', '')
        )
        db.session.add(paper)
        db.session.commit()
        
        # 异步分配专家
        threading.Thread(target=assign_paper_to_experts, args=(paper.id,)).start()
        
        return jsonify({
            "message": "论文提交成功，正在分配专家进行评审",
            "paper_id": paper.id,
            "status": "submitted"
        }), 201
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# 分配论文给专家
def assign_paper_to_experts(paper_id):
    try:
        # 等待一段时间，确保事务已提交
        time.sleep(1)
        
        paper = Paper.query.get(paper_id)
        if not paper:
            print(f"论文不存在: {paper_id}")
            return False
        
        # 调用专家服务获取可用专家
        expert_service_url = os.environ.get('EXPERT_SERVICE_URL', 'http://expert-service:5001')
        experts_response = requests.get(
            f"{expert_service_url}/experts/available?min_papers=0&max_papers=9"
        )
        
        if experts_response.status_code != 200:
            print(f"获取可用专家失败: {experts_response.status_code}")
            return False
        
        experts = experts_response.json().get('experts', [])
        
        # 如果可用专家不足3人，则无法分配
        if len(experts) < 3:
            print(f"可用专家不足，无法分配论文 {paper_id}")
            return False
        
        # 根据论文关键词和专家专长进行匹配
        matched_experts = []
        for expert in experts:
            expertise = expert.get('expertise', '').lower()
            keywords = paper.keywords.lower() if paper.keywords else ''
            
            # 简单匹配算法：检查专家专长是否包含论文关键词中的任何一个
            if any(keyword in expertise for keyword in keywords.split(',')):
                matched_experts.append(expert)
        
        # 如果匹配的专家不足3人，则从所有可用专家中随机选择
        if len(matched_experts) < 3:
            # 按当前论文数量排序，优先分配给论文数量少的专家
            sorted_experts = sorted(experts, key=lambda x: x.get('current_papers', 0))
            matched_experts = sorted_experts[:3]
        else:
            # 如果匹配的专家超过3人，选择当前论文数量最少的3位
            matched_experts = sorted(matched_experts, key=lambda x: x.get('current_papers', 0))[:3]
        
        # 分配给选中的专家
        for expert in matched_experts:
            assignment = ExpertPaperAssignment(
                expert_id=expert['id'],
                expert_name=expert['name'],
                paper_id=paper.id,
                paper_title=paper.title
            )
            db.session.add(assignment)
        
        # 更新论文状态
        paper.status = 'assigned'
        db.session.commit()
        
        # 发送分配通知
        for expert in matched_experts:
            # 发布消息到RabbitMQ
            publish_message('paper.assignment', {
                'expert_id': expert['id'],
                'paper_id': paper.id,
                'paper_title': paper.title
            })
            
            # 调用通知服务
            try:
                notification_service_url = os.environ.get('NOTIFICATION_SERVICE_URL', 'http://notification-service:5004')
                requests.post(
                    f"{notification_service_url}/notify",
                    json={
                        "recipient_id": expert['id'],
                        "recipient_type": "expert",
                        "message": f"您有新的论文评审任务: {paper.title}",
                        "subject": "新的评审任务",
                        "email": expert.get('email')
                    }
                )
            except Exception as e:
                print(f"通知专家失败: {e}")
        
        print(f"论文 {paper.id} 已成功分配给 {len(matched_experts)} 位专家")
        return True
    
    except Exception as e:
        db.session.rollback()
        print(f"分配论文时发生错误: {e}")
        return False

# 获取论文详情
@app.route('/papers/<int:paper_id>', methods=['GET'])
def get_paper(paper_id):
    paper = Paper.query.get_or_404(paper_id)
    
    # 获取论文的评审分配情况
    assignments = ExpertPaperAssignment.query.filter_by(paper_id=paper_id).all()
    experts = []
    
    for assignment in assignments:
        expert_info = {
            "id": assignment.expert_id,
            "name": assignment.expert_name,
            "assignment_id": assignment.id,
            "status": assignment.status,
            "score": assignment.score,
            "comment": assignment.comment,
            "assigned_date": assignment.assigned_date.isoformat() if assignment.assigned_date else None,
            "completed_date": assignment.completed_date.isoformat() if assignment.completed_date else None
        }
        experts.append(expert_info)
    
    return jsonify({
        "id": paper.id,
        "title": paper.title,
        "author_id": paper.author_id,
        "author_name": paper.author_name,
        "abstract": paper.abstract,
        "keywords": paper.keywords,
        "file_path": paper.file_path,
        "status": paper.status,
        "submission_date": paper.submission_date.isoformat() if paper.submission_date else None,
        "average_score": paper.average_score,
        "review_completed_date": paper.review_completed_date.isoformat() if paper.review_completed_date else None,
        "experts": experts
    })

# 获取论文列表
@app.route('/papers', methods=['GET'])
def get_papers():
    # 支持按状态、作者ID筛选
    status = request.args.get('status')
    author_id = request.args.get('author_id')
    
    query = Paper.query
    
    if status:
        query = query.filter_by(status=status)
    
    if author_id:
        query = query.filter_by(author_id=int(author_id))
    
    papers = query.order_by(Paper.submission_date.desc()).all()
    result = []
    
    for paper in papers:
        result.append({
            "id": paper.id,
            "title": paper.title,
            "author_name": paper.author_name,
            "status": paper.status,
            "submission_date": paper.submission_date.isoformat() if paper.submission_date else None,
            "average_score": paper.average_score
        })
    
    return jsonify({"papers": result})

# 获取专家的论文列表
@app.route('/experts/<int:expert_id>/papers', methods=['GET'])
def get_expert_papers(expert_id):
    # 支持按状态筛选
    status = request.args.get('status')
    
    query = ExpertPaperAssignment.query.filter_by(expert_id=expert_id)
    
    if status:
        query = query.filter_by(status=status)
    
    assignments = query.order_by(ExpertPaperAssignment.assigned_date.desc()).all()
    result = []
    
    for assignment in assignments:
        paper = Paper.query.get(assignment.paper_id)
        if paper:
            result.append({
                "assignment_id": assignment.id,
                "paper_id": paper.id,
                "title": paper.title,
                "author_name": paper.author_name,
                "status": assignment.status,
                "assigned_date": assignment.assigned_date.isoformat() if assignment.assigned_date else None,
                "score": assignment.score,
                "paper_status": paper.status
            })
    
    return jsonify({"papers": result})

# 获取专家评审数量统计
@app.route('/experts/paper-counts', methods=['GET'])
def get_expert_paper_counts():
    # 统计每个专家分配的论文数量
    counts = db.session.query(
        ExpertPaperAssignment.expert_id,
        func.count(ExpertPaperAssignment.id).label('paper_count')
    ).group_by(ExpertPaperAssignment.expert_id).all()
    
    result = []
    for count in counts:
        result.append({
            "expert_id": count[0],
            "paper_count": count[1]
        })
    
    return jsonify({"counts": result})

# 更新论文分配状态
@app.route('/assignments/<int:assignment_id>', methods=['PUT'])
def update_assignment(assignment_id):
    data = request.json
    assignment = ExpertPaperAssignment.query.get_or_404(assignment_id)
    
    # 更新状态
    if 'status' in data:
        assignment.status = data['status']
    
    # 更新评分和评审意见
    if 'score' in data:
        assignment.score = data['score']
    
    if 'comment' in data:
        assignment.comment = data['comment']
    
    # 如果状态为completed，设置完成时间
    if assignment.status == 'completed' and not assignment.completed_date:
        assignment.completed_date = func.now()
    
    db.session.commit()
    
    # 检查是否所有专家都已完成评审
    check_paper_review_status(assignment.paper_id)
    
    return jsonify({
        "message": "Assignment updated successfully",
        "assignment_id": assignment.id,
        "status": assignment.status
    })

# 检查论文评审状态
def check_paper_review_status(paper_id):
    paper = Paper.query.get(paper_id)
    if not paper:
        return
    
    # 获取该论文的所有评审分配
    assignments = ExpertPaperAssignment.query.filter_by(paper_id=paper_id).all()
    
    # 检查是否所有专家都已完成评审
    all_completed = all(assignment.status == 'completed' for assignment in assignments)
    
    if all_completed and len(assignments) > 0:
        # 计算平均分
        total_score = sum(assignment.score for assignment in assignments if assignment.score is not None)
        average = total_score / len(assignments)
        
        # 更新论文状态和平均分
        paper.status = 'reviewed'
        paper.average_score = average
        paper.review_completed_date = func.now()
        db.session.commit()
        
        # 发送通知给论文作者
        publish_message('paper.review', {
            'paper_id': paper.id,
            'paper_title': paper.title,
            'author_id': paper.author_id,
            'average_score': average
        })
        
        # 调用通知服务
        try:
            notification_service_url = os.environ.get('NOTIFICATION_SERVICE_URL', 'http://notification-service:5004')
            requests.post(
                f"{notification_service_url}/notify",
                json={
                    "recipient_id": paper.author_id,
                    "recipient_type": "author",
                    "message": f"您的论文《{paper.title}》已完成评审，平均分为: {average:.2f}",
                    "subject": "论文评审结果"
                }
            )
        except Exception as e:
            print(f"通知论文作者失败: {e}")

# 更新论文平均分
@app.route('/papers/<int:paper_id>/score', methods=['PUT'])
def update_paper_score(paper_id):
    data = request.json
    paper = Paper.query.get_or_404(paper_id)
    
    if 'average_score' in data:
        paper.average_score = data['average_score']
        paper.status = 'reviewed'
        paper.review_completed_date = func.now()
        db.session.commit()
    
    return jsonify({
        "message": "Paper score updated successfully",
        "paper_id": paper.id,
        "average_score": paper.average_score
    })

# 获取论文统计数据
@app.route('/statistics', methods=['GET'])
def get_statistics():
    # 统计各状态论文数量
    status_counts = db.session.query(
        Paper.status,
        func.count(Paper.id).label('count')
    ).group_by(Paper.status).all()
    
    # 统计平均分分布
    score_distribution = db.session.query(
        func.round(Paper.average_score / 10).label('score_range'),
        func.count(Paper.id).label('count')
    ).filter(Paper.average_score.isnot(None)).group_by('score_range').all()
    
    # 转换为前端友好的格式
    status_stats = {status: count for status, count in status_counts}
    
    score_stats = {}
    for score_range, count in score_distribution:
        range_key = f"{int(score_range) * 10}-{int(score_range) * 10 + 9}"
        score_stats[range_key] = count
    
    return jsonify({
        "status_statistics": status_stats,
        "score_statistics": score_stats,
        "total_papers": Paper.query.count(),
        "total_assignments": ExpertPaperAssignment.query.count(),
        "completed_reviews": ExpertPaperAssignment.query.filter_by(status='completed').count()
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)