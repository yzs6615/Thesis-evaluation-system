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
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///notifications.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
CORS(app)

# 通知模型
class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)  # 接收通知的用户ID
    type = db.Column(db.String(50), nullable=False)  # 通知类型
    title = db.Column(db.String(200), nullable=False)  # 通知标题
    message = db.Column(db.Text, nullable=False)  # 通知内容
    data = db.Column(db.JSON)  # 附加数据
    is_read = db.Column(db.Boolean, default=False)  # 是否已读
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
        channel.queue_declare(queue='notification_events', durable=True)
        
        # 绑定队列到交换机，监听所有事件
        channel.queue_bind(exchange='thesis_events', queue='notification_events', routing_key='#')
        
        return connection, channel
    except Exception as e:
        print(f"RabbitMQ连接失败: {e}")
        return None, None

# 处理消息队列中的事件
def process_event(ch, method, properties, body):
    try:
        event = json.loads(body)
        routing_key = method.routing_key
        print(f"收到事件: {routing_key} - {event}")
        
        # 根据事件类型创建通知
        if routing_key == 'paper.submitted':
            # 论文提交事件 - 通知管理员
            admin_users = get_admin_users()
            paper_id = event.get('paper_id')
            title = event.get('title', '未知论文')
            author_id = event.get('author_id')
            
            for admin in admin_users:
                create_notification(
                    user_id=admin['id'],
                    type='paper_submitted',
                    title='新论文提交',
                    message=f"新论文《{title}》已提交，等待分配专家评审。",
                    data={
                        'paper_id': paper_id,
                        'author_id': author_id
                    }
                )
        
        elif routing_key == 'paper.assigned':
            # 论文分配事件 - 通知专家
            expert_id = event.get('expert_id')
            paper_id = event.get('paper_id')
            paper_title = event.get('title', '未知论文')
            
            create_notification(
                user_id=expert_id,
                type='paper_assigned',
                title='新论文评审任务',
                message=f"您被分配了一篇新论文《{paper_title}》进行评审。",
                data={
                    'paper_id': paper_id
                }
            )
        
        elif routing_key == 'score.submitted':
            # 评分提交事件 - 通知管理员
            admin_users = get_admin_users()
            paper_id = event.get('paper_id')
            expert_id = event.get('expert_id')
            score = event.get('score')
            
            # 获取论文和专家信息
            paper_title = get_paper_title(paper_id)
            expert_name = get_expert_name(expert_id)
            
            for admin in admin_users:
                create_notification(
                    user_id=admin['id'],
                    type='score_submitted',
                    title='评分已提交',
                    message=f"专家 {expert_name} 已为论文《{paper_title}》提交评分: {score}分。",
                    data={
                        'paper_id': paper_id,
                        'expert_id': expert_id,
                        'score': score
                    }
                )
        
        elif routing_key == 'score.paper_reviewed':
            # 论文评审完成事件 - 通知作者和管理员
            paper_id = event.get('paper_id')
            average_score = event.get('average_score')
            
            # 获取论文信息
            paper_info = get_paper_info(paper_id)
            if paper_info:
                paper_title = paper_info.get('title', '未知论文')
                author_id = paper_info.get('author_id')
                
                # 通知作者
                create_notification(
                    user_id=author_id,
                    type='paper_reviewed',
                    title='论文评审完成',
                    message=f"您的论文《{paper_title}》已完成评审，平均分为{average_score}分。",
                    data={
                        'paper_id': paper_id,
                        'average_score': average_score
                    }
                )
                
                # 通知管理员
                admin_users = get_admin_users()
                for admin in admin_users:
                    create_notification(
                        user_id=admin['id'],
                        type='paper_reviewed',
                        title='论文评审完成',
                        message=f"论文《{paper_title}》已完成评审，平均分为{average_score}分。",
                        data={
                            'paper_id': paper_id,
                            'average_score': average_score
                        }
                    )
        
        # 确认消息处理完成
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"处理事件失败: {e}")
        # 消息处理失败，重新入队
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

# 获取管理员用户列表
def get_admin_users():
    try:
        auth_service_url = os.environ.get('AUTH_SERVICE_URL', 'http://auth-service:5003')
        response = requests.get(f"{auth_service_url}/users?role=admin")
        if response.status_code == 200:
            return response.json().get('users', [])
        return []
    except Exception as e:
        print(f"获取管理员用户失败: {e}")
        return []

# 获取论文标题
def get_paper_title(paper_id):
    try:
        paper_service_url = os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000')
        response = requests.get(f"{paper_service_url}/papers/{paper_id}")
        if response.status_code == 200:
            return response.json().get('title', '未知论文')
        return '未知论文'
    except Exception as e:
        print(f"获取论文标题失败: {e}")
        return '未知论文'

# 获取专家姓名
def get_expert_name(expert_id):
    try:
        expert_service_url = os.environ.get('EXPERT_SERVICE_URL', 'http://expert-service:5001')
        response = requests.get(f"{expert_service_url}/experts/{expert_id}")
        if response.status_code == 200:
            return response.json().get('name', '未知专家')
        return '未知专家'
    except Exception as e:
        print(f"获取专家姓名失败: {e}")
        return '未知专家'

# 获取论文信息
def get_paper_info(paper_id):
    try:
        paper_service_url = os.environ.get('PAPER_SERVICE_URL', 'http://paper-service:5000')
        response = requests.get(f"{paper_service_url}/papers/{paper_id}")
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"获取论文信息失败: {e}")
        return None

# 创建通知
def create_notification(user_id, type, title, message, data=None):
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        data=data
    )
    db.session.add(notification)
    db.session.commit()
    return notification

# 启动消息监听
def start_consuming():
    connection, channel = connect_to_rabbitmq()
    if channel:
        # 设置每次只处理一条消息
        channel.basic_qos(prefetch_count=1)
        # 注册消息处理函数
        channel.basic_consume(queue='notification_events', on_message_callback=process_event)
        # 开始监听
        print("开始监听消息队列...")
        channel.start_consuming()

# 创建通知
@app.route('/notifications', methods=['POST'])
def create_notification_api():
    data = request.json
    
    # 检查必要字段
    required_fields = ['user_id', 'type', 'title', 'message']
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"缺少必要字段: {field}"}), 400
    
    notification = create_notification(
        user_id=data['user_id'],
        type=data['type'],
        title=data['title'],
        message=data['message'],
        data=data.get('data')
    )
    
    return jsonify({
        "message": "通知创建成功",
        "notification_id": notification.id
    }), 201

# 获取用户通知
@app.route('/users/<int:user_id>/notifications', methods=['GET'])
def get_user_notifications(user_id):
    # 验证请求者身份
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
        
        # 检查用户是否有权限查看通知
        if auth_data.get('role') != 'admin' and auth_data.get('user_id') != user_id:
            return jsonify({"error": "无权限查看此用户通知"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    # 支持分页
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))
    
    # 支持按类型筛选
    notification_type = request.args.get('type')
    
    # 支持按已读/未读筛选
    is_read = request.args.get('is_read')
    if is_read is not None:
        is_read = is_read.lower() == 'true'
    
    query = Notification.query.filter_by(user_id=user_id)
    
    if notification_type:
        query = query.filter_by(type=notification_type)
    
    if is_read is not None:
        query = query.filter_by(is_read=is_read)
    
    # 按创建时间倒序排序
    query = query.order_by(Notification.created_at.desc())
    
    # 分页
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)
    
    notifications = []
    for notification in paginated.items:
        notifications.append({
            "id": notification.id,
            "type": notification.type,
            "title": notification.title,
            "message": notification.message,
            "data": notification.data,
            "is_read": notification.is_read,
            "created_at": notification.created_at.isoformat() if notification.created_at else None
        })
    
    return jsonify({
        "notifications": notifications,
        "total": paginated.total,
        "pages": paginated.pages,
        "current_page": page
    })

# 标记通知为已读
@app.route('/notifications/<int:notification_id>/read', methods=['PUT'])
def mark_notification_read(notification_id):
    # 验证请求者身份
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    
    if not token:
        return jsonify({"error": "需要认证令牌"}), 401
    
    notification = Notification.query.get_or_404(notification_id)
    
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
        
        # 检查用户是否有权限标记通知
        if auth_data.get('role') != 'admin' and auth_data.get('user_id') != notification.user_id:
            return jsonify({"error": "无权限标记此通知"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    notification.is_read = True
    db.session.commit()
    
    return jsonify({
        "message": "通知已标记为已读",
        "notification_id": notification.id
    })

# 标记所有通知为已读
@app.route('/users/<int:user_id>/notifications/read-all', methods=['PUT'])
def mark_all_notifications_read(user_id):
    # 验证请求者身份
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
        
        # 检查用户是否有权限标记通知
        if auth_data.get('role') != 'admin' and auth_data.get('user_id') != user_id:
            return jsonify({"error": "无权限标记此用户通知"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    # 支持按类型筛选
    notification_type = request.args.get('type')
    
    query = Notification.query.filter_by(user_id=user_id, is_read=False)
    
    if notification_type:
        query = query.filter_by(type=notification_type)
    
    notifications = query.all()
    count = len(notifications)
    
    for notification in notifications:
        notification.is_read = True
    
    db.session.commit()
    
    return jsonify({
        "message": f"{count}条通知已标记为已读",
        "count": count
    })

# 获取未读通知数量
@app.route('/users/<int:user_id>/notifications/unread-count', methods=['GET'])
def get_unread_count(user_id):
    # 验证请求者身份
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
        
        # 检查用户是否有权限查看通知
        if auth_data.get('role') != 'admin' and auth_data.get('user_id') != user_id:
            return jsonify({"error": "无权限查看此用户通知"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    # 支持按类型筛选
    notification_type = request.args.get('type')
    
    query = Notification.query.filter_by(user_id=user_id, is_read=False)
    
    if notification_type:
        query = query.filter_by(type=notification_type)
    
    count = query.count()
    
    return jsonify({
        "user_id": user_id,
        "unread_count": count
    })

# 删除通知
@app.route('/notifications/<int:notification_id>', methods=['DELETE'])
def delete_notification(notification_id):
    # 验证请求者身份
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    
    if not token:
        return jsonify({"error": "需要认证令牌"}), 401
    
    notification = Notification.query.get_or_404(notification_id)
    
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
        
        # 检查用户是否有权限删除通知
        if auth_data.get('role') != 'admin' and auth_data.get('user_id') != notification.user_id:
            return jsonify({"error": "无权限删除此通知"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    db.session.delete(notification)
    db.session.commit()
    
    return jsonify({
        "message": "通知已删除",
        "notification_id": notification_id
    })

# 获取通知统计数据
@app.route('/statistics', methods=['GET'])
def get_statistics():
    # 验证请求者身份
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
        
        # 只有管理员可以查看统计数据
        if auth_data.get('role') != 'admin':
            return jsonify({"error": "无权限查看统计数据"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    # 统计通知总数
    total_notifications = Notification.query.count()
    
    # 统计未读通知数
    unread_count = Notification.query.filter_by(is_read=False).count()
    
    # 统计各类型通知数量
    type_counts = db.session.query(
        Notification.type,
        func.count(Notification.id).label('count')
    ).group_by(Notification.type).all()
    
    # 转换为前端友好的格式
    type_stats = {type_name: count for type_name, count in type_counts}
    
    return jsonify({
        "total_notifications": total_notifications,
        "unread_count": unread_count,
        "type_statistics": type_stats
    })

# 在单独的线程中启动消息监听
import threading
if __name__ == '__main__':
    # 启动消息监听线程
    consumer_thread = threading.Thread(target=start_consuming)
    consumer_thread.daemon = True
    consumer_thread.start()
    
    # 启动Web服务
    app.run(host='0.0.0.0', port=5004, debug=True)