FROM kaizen/base:latest
# Browsers outside /root so the non-root container user can run them.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir playwright
RUN playwright install --with-deps chromium
COPY app.py /app/app.py
WORKDIR /app
CMD ["python", "app.py"]
