from flask import Flask, request, jsonify
import json

app = Flask(__name__)

# 模拟用户数据库
users = []
papers = []

# 登录接口
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    for user in users:
        if user['username'] == username and user['password'] == password:
            return jsonify({'message': '登录成功'}), 200
    return jsonify({'message': '用户名或密码错误'}), 401

# 注册接口
@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    for user in users:
        if user['username'] == username:
            return jsonify({'message': '用户名已存在'}), 400
    new_user = {
        'username': username,
        'email': email,
        'password': password
    }
    users.append(new_user)
    return jsonify({'message': '注册成功'}), 201

# 提交论文接口
@app.route('/submit_paper', methods=['POST'])
def submit_paper():
    data = request.get_json()
    title = data.get('title')
    abstract = data.get('abstract')
    new_paper = {
        'title': title,
        'abstract': abstract
    }
    papers.append(new_paper)
    return jsonify({'message': '论文提交成功'}), 201

if __name__ == '__main__':
    app.run(debug=True)