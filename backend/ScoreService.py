import os
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import requests

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
    score = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

# 创建数据库表
with app.app_context():
    db.create_all()

@app.route('/papers/<int:paper_id>/scores', methods=['POST'])
def submit_score(paper_id):
    data = request.json
    
    # 验证专家身份
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"error": "Authorization header missing"}), 401
    
    try:
        # 调用认证服务验证令牌
        auth_response = requests.get(
            "http://auth-service:5003/verify",
            headers={"Authorization": auth_header}
        )
        if auth_response.status_code != 200:
            return jsonify({"error": "Invalid token"}), 401
        
        user_data = auth_response.json()
        expert_id = user_data.get('external_id')
        
        # 验证专家是否被分配了这篇论文
        assignment_response = requests.get(
            f"http://paper-service:5000/papers/{paper_id}/experts"
        )
        if assignment_response.status_code != 200:
            return jsonify({"error": "Invalid paper ID"}), 404
        
        experts = assignment_response.json().get('experts', [])
        expert_assigned = False
        assignment_id = None
        
        for expert in experts:
            if expert['id'] == expert_id:
                expert_assigned = True
                assignment_id = expert['assignment_id']
                break
        
        if not expert_assigned:
            return jsonify({"error": "You are not assigned to review this paper"}), 403
        
        # 验证分数范围
        score_value = data.get('score')
        if not isinstance(score_value, int) or score_value < 0 or score_value > 100:
            return jsonify({"error": "Score must be an integer between 0 and 100"}), 400
        
        # 保存评分
        score = Score(
            paper_id=paper_id,
            expert_id=expert_id,
            score=score_value,
            comment=data.get('comment', '')
        )
        db.session.add(score)
        db.session.commit()
        
        # 更新论文分配状态
        try:
            update_response = requests.put(
                f"http://paper-service:5000/assignments/{assignment_id}",
                json={
                    "status": "completed",
                    "score": score_value,
                    "comment": data.get('comment', '')
                }
            )
            if update_response.status_code != 200:
                print(f"更新论文分配状态失败: {update_response.status_code}")
        except Exception as e:
            print(f"更新论文分配状态时发生错误: {e}")
        
        # 检查是否所有专家都已评分，如果是则计算平均分
        try:
            check_response = requests.get(
                f"http://paper-service:5000/papers/{paper_id}/experts"
            )
            if check_response.status_code == 200:
                experts = check_response.json().get('experts', [])
                all_completed = all(expert['status'] == 'completed' for expert in experts)
                
                if all_completed:
                    # 计算平均分
                    scores_response = requests.get(
                        f"http://scoring-service:5002/papers/{paper_id}/scores"
                    )
                    if scores_response.status_code == 200:
                        scores = scores_response.json().get('scores', [])
                        if scores:
                            total = sum(score['score'] for score in scores)
                            average = total / len(scores)
                            
                            # 更新论文平均分
                            update_paper_response = requests.put(
                                f"http://paper-service:5000/papers/{paper_id}/score",
                                json={"average_score": average}
                            )
                            if update_paper_response.status_code == 200:
                                # 通知论文作者
                                try:
                                    requests.post(
                                        "http://notification-service:5004/notify",
                                        json={
                                            "recipient_id": paper_id,
                                            "recipient_type": "paper",
                                            "message": f"您的论文已完成评审，平均分为: {average:.2f}",
                                            "subject": "论文评审结果"
                                        }
                                    )
                                except Exception as e:
                                    print(f"通知论文作者失败: {e}")
        
        except Exception as e:
            print(f"检查评分状态时发生错误: {e}")
        
        return jsonify({
            "message": "Score submitted successfully",
            "paper_id": paper_id,
            "expert_id": expert_id,
            "score": score_value
        }), 201
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/papers/<int:paper_id>/scores', methods=['GET'])
def get_paper_scores(paper_id):
    scores = Score.query.filter_by(paper_id=paper_id).all()
    score_list = []
    
    for score in scores:
        expert_info = {}
        try:
            expert_response = requests.get(f"http://expert-service:5001/experts/{score.expert_id}")
            if expert_response.status_code == 200:
                expert_info = expert_response.json()
        except Exception as e:
            print(f"获取专家信息失败: {e}")
        
        score_list.append({
            "id": score.id,
            "paper_id": score.paper_id,
            "expert_id": score.expert_id,
            "expert_name": expert_info.get('name', 'Unknown'),
            "score": score.score,
            "comment": score.comment,
            "created_at": score.created_at.isoformat()
        })
    
    return jsonify({"scores": score_list})

@app.route('/papers/<int:paper_id>/average', methods=['GET'])
def get_paper_average_score(paper_id):
    scores = Score.query.filter_by(paper_id=paper_id).all()
    if not scores:
        return jsonify({"average_score": None})
    
    total = sum(score.score for score in scores)
    average = total / len(scores)
    
    return jsonify({"average_score": average})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)
