# The Streamlit application with the committed marts and classifier model.
# docker build -t procurement-intelligence .
# docker run -p 8501:8501 -e GEMINI_API_KEY=... procurement-intelligence
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY .streamlit .streamlit
COPY src src
COPY app app
COPY models models
COPY reports reports
COPY data/marts data/marts

# Public demo mode: each visitor's decisions stay in their session.
ENV PUBLIC_DEMO=true
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
