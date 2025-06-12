import os
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import requests

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///papers.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
CORS(app)

# 论文模型
class Paper(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(100), nullable=False)
    abstract = db.Column(db.Text)
    file_path = db.Column(db.String(200))
    status = db.Column(db.String(50), default='submitted')  # submitted, assigned, reviewed
    average_score = db.Column(db.Float, default=None)

# 专家-论文分配模型
class ExpertPaperAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    expert_id = db.Column(db.Integer, nullable=False)
    paper_id = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(50), default='assigned')  # assigned, in_progress, completed
    score = db.Column(db.Integer, default=None)
    comment = db.Column(db.Text)

# 创建数据库表
with app.app_context():
    db.create_all()

@app.route('/papers', methods=['POST'])
def submit_paper():
    data = request.json
    paper = Paper(
        title=data['title'],
        author=data['author'],
        abstract=data.get('abstract', ''),
        file_path=data.get('file_path', '')
    )
    db.session.add(paper)
    db.session.commit()
    
    # 调用分配服务
    assign_paper_to_experts(paper.id)
    
    return jsonify({"message": "Paper submitted successfully", "paper_id": paper.id}), 201

def assign_paper_to_experts(paper_id):
    # 调用专家管理服务获取可用专家
    try:
        experts_response = requests.get(f"http://expert-service:5001/experts/available?papers=3")
        if experts_response.status_code == 200:
            experts = experts_response.json()['experts']
            if len(experts) >= 3:
                # 分配给3位专家
                for expert in experts[:3]:
                    assignment = ExpertPaperAssignment(
                        expert_id=expert['id'],
                        paper_id=paper_id
                    )
                    db.session.add(assignment)
                    
                    # 通知专家
                    try:
                        requests.post(
                            "http://notification-service:5004/notify",
                            json={
                                "recipient_id": expert['id'],
                                "recipient_type": "expert",
                                "message": f"您有新的论文评审任务: {paper_id}",
                                "subject": "新的评审任务"
                            }
                        )
                    except Exception as e:
                        print(f"通知专家失败: {e}")
                
                db.session.commit()
                return True
            else:
                print(f"可用专家不足，无法分配论文 {paper_id}")
                return False
        else:
            print(f"获取可用专家失败: {experts_response.status_code}")
            return False
    except Exception as e:
        print(f"分配论文时发生错误: {e}")
        return False

@app.route('/papers/<int:paper_id>/experts', methods=['GET'])
def get_paper_experts(paper_id):
    assignments = ExpertPaperAssignment.query.filter_by(paper_id=paper_id).all()
    experts = []
    for assignment in assignments:
        try:
            expert_response = requests.get(f"http://expert-service:5001/experts/{assignment.expert_id}")
            if expert_response.status_code == 200:
                expert = expert_response.json()
                expert['assignment_id'] = assignment.id
                expert['status'] = assignment.status
                expert['score'] = assignment.score
                expert['comment'] = assignment.comment
                experts.append(expert)
        except Exception as e:
            print(f"获取专家信息失败: {e}")
    
    return jsonify({"experts": experts})

@app.route('/papers/<int:paper_id>/status', methods=['GET'])
def get_paper_status(paper_id):
    paper = Paper.query.get_or_404(paper_id)
    return jsonify({
        "paper_id": paper.id,
        "title": paper.title,
        "status": paper.status,
        "average_score": paper.average_score
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
