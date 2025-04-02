![image](https://github.com/junyang168/smart-answer/assets/15166180/8793353e-b56c-402d-bf77-d141a16d50ff)# Smart Answer
 Production-ready enterprise Question and Answer(Q&A) chatbot that enables natural language interface to your enterprise applications.
# Overview
Generative AI's adoption in enterprise faces seveal challenge. One major challenge is LLMs' inability to access proprietary enterprise knowledge, as enterprises often have their data scattered across various applications, each with unique business logic and interfaces. Another concern is data security and privacy when using public LLMs, as many companies require AI systems that can operate on corporate-owned infrastructure.

Smart Answer, a production-ready enterprise Question and Answer (Q&A) chatbot is designed to provide accurate answers by sourcing knowledge from multiple enterprise applications. It integrates quickly with these applications, speeding up its deployment. Smart Answer operates on both public and smaller, open-source LLMs that can be hosted in-house on consumer-grade hardware, ensuring data security and privacy.

# Solution Design
Smart Answer is based on Retrieval Augmented Generation, a popular architecture of Generative AI. It emulates the human process of selecting and using IT tools to answer queries to routes queries to the most suitable 'tool' or application, retrieves relevant data, and generates answers. The system also includes optimizations for enhancing answer precision. The chatbot presents answers to users along with links to data sources for reference.
[Please refer to the technical overview for solution architecture and design detail](https://medium.com/@junyang168/smart-answer-turning-enterprise-applications-into-ai-powered-chatbot-4b1aabce6c9d)

# Usage
Plesae refer to the following Jupyter Notebooks 
* [Overview and Routing](https://github.com/junyang168/smart-answer/blob/main/routing.ipynb)  

# Contributing
We welcome contributions to Smart Answers. If you are interested in contributing, please contact us for more information.

# License
Smart Answers is licensed under the Apache 2.0 License.

# Folders and Files
* production installed at /opt/homebrew/var/www/smart-answer
* both prod and dev venv at /Users/junyang/app/smart-answer/.venv
# Semantic Search API
* port 8000 - dev 8080
* /Users/junyang/Library/LaunchAgents/com.semantic_search.service.plist
* launchctl load ~/Library/LaunchAgents/semantic_search.service.plist 
* launchctl start semantic_search.service
* launchctl unload ~/Library/LaunchAgents/semantic_search.service.plist
* launchctl list |grep semantic_search.service
* uvicorn --reload --host 0.0.0.0  --port 9000 --app-dir /Users/junyang/app/smart-answer/src/semantic_search_service api:app
# Smart Answer API
* /Users/junyang/Library/LaunchAgents/com.smart_answer.service.plist
* launchctl unload ~/Library/LaunchAgents/com.smart_answer.service.plist
* launchctl start smart_answer.service
* launchctl load ~/Library/LaunchAgents/com.smart_answer.service.plist
* uvicorn --reload --host 0.0.0.0  --port 60000 --app-dir /opt/homebrew/var/www/smart-answer/src/smart_answer_service smart_answer_api:app
# Smart Answer UI
* production port 60000
* launchctl unload ~/Library/LaunchAgents/com.sa_app.service.plist
* launchctl load ~/Library/LaunchAgents/com.sa_app.service.plist 
* launchctl start sa_app.service

dev port 3003

