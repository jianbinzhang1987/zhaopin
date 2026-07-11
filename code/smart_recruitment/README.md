# 智能招聘评测系统 MVP

本目录是根据 `设计文档` 中的产品原型和轻量技术架构落地的 Django 单体项目。

## 本地运行

```bash
cd /Users/adolf/Desktop/code/招聘/code/smart_recruitment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8000
```

演示账号：

- 用户名：`admin`
- 密码：`admin123456`

## 已实现页面

- P01 招聘任务列表
- P02 新建招聘评测任务
- P03 简历能力匹配页
- P04 普通题目审核与导出
- P05 现场开发题交付
- P06 评分与评测报告

页面布局以 `code/prototypes` 中从产品文档提取的原型图为对照实现。
