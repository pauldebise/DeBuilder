"""Interface web FastAPI de DeBuilder.

Remplace l'ancienne interface Gradio (src/gui/*.py) par une app
FastAPI + frontend HTML/JS vanilla (src/web/static/), avec reprise
de session au reload et flux de logs en SSE.
"""
