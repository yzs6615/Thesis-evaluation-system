import os
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import requests

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
    expertise = db.Column(db.String(200))
    password_hash = db.Column(db.String(200))  # 实际应用中应该加密存储

# 专家-论文分配视图
class ExpertPaperView(db.Model):
    __tablename__ = 'v_expert_papers'
    __table_args__ = (
        db.Text('SELECT expert_id, COUNT(*) as paper_count FROM expert_paper_assignments GROUP BY expert_id'),
        {}
    )
    expert_id = db.Column(db.Integer, primary_key=True)
    paper_count = db.Column(db.Integer)

# 创建数据库表
with app.app_context():
    db.create_all()

@app.route('/experts', methods=['POST'])
def register_expert():
    data = request.json
    expert = Expert(
        name=data['name'],
        email=data['email'],
        expertise=data.get('expertise', ''),
        password_hash=data['password']  # 实际应用中应该加密
    )
    db.session.add(expert)
    db.session.commit()
    
    # 调用认证服务创建账户
    try:
        auth_response = requests.post(
            "http://auth-service:5003/users",
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
            return jsonify({"error": "Failed to create authentication account"}), 500
    except Exception as e:
        # 如果认证服务失败，回滚专家注册
        db.session.delete(expert)
        db.session.commit()
        return jsonify({"error": f"Authentication service error: {str(e)}"}), 500
    
    return jsonify({"message": "Expert registered successfully", "expert_id": expert.id}), 201

@app.route('/experts/<int:expert_id>', methods=['GET'])
def get_expert(expert_id):
    expert = Expert.query.get_or_404(expert_id)
    return jsonify({
        "id": expert.id,
        "name": expert.name,
        "email": expert.email,
        "expertise": expert.expertise
    })

@app.route('/experts/available', methods=['GET'])
def get_available_experts():
    min_papers = int(request.args.get('min_papers', 0))
    max_papers = int(request.args.get('max_papers', 10))
    
    # 获取所有专家
    experts = Expert.query.all()
    
    # 获取每个专家当前分配的论文数量
    paper_counts = {}
    try:
        counts_response = requests.get("http://paper-service:5000/experts/paper-counts")
        if counts_response.status_code == 200:
            for count in counts_response.json()['counts']:
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
                "current_papers": count
            })
    
    return jsonify({"experts": available_experts})

@app.route('/experts/<int:expert_id>/papers', methods=['GET'])
def get_expert_papers(expert_id):
    try:
        papers_response = requests.get(f"http://paper-service:5000/experts/{expert_id}/papers")
        if papers_response.status_code == 200:
            return papers_response.json()
        else:
            return jsonify({"error": "Failed to get papers"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
