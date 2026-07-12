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

## 大模型接入

后台 Worker 支持 OpenAI-compatible 的 `/chat/completions` 接口。配置环境变量后，岗位/简历分析、普通题生成、现场开发题生成、AI辅助评分和报告草稿都会调用模型；未配置 `LLM_API_KEY` 时会使用本地兜底逻辑，保证业务流程仍可跑通。

```bash
export LLM_API_KEY="你的API Key"
export LLM_MODEL="gpt-4.1-mini"
export LLM_BASE_URL="https://api.openai.com/v1"
python manage.py run_worker
```

也可以把 `LLM_BASE_URL` 指向私有模型网关，只要它兼容 OpenAI Chat Completions 响应格式。

## 当前闭环

1. 创建任务并上传简历
2. Worker 解析简历并调用模型生成能力匹配，随后自动生成普通题和现场开发题建议（进入待确认验证项阶段）
3. 确认验证项后进入题目审核
4. 审核并交付普通题与现场开发题
5. 上传普通题答卷和开发题结果
6. Worker 调用模型生成两类 AI 建议分
7. Worker 调用模型生成最终报告草稿
8. 人工确认报告后任务完成

## 常用命令

```bash
python manage.py run_worker          # 常驻轮询后台任务
python manage.py run_worker --once   # 只处理一个任务后退出
```
