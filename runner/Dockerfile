# Chris Li · 技能沙箱 runner（无网络、无密钥、每任务独立 UID）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# 可选镜像源（由 docker-compose 从项目根 .env 传入；留空 = 官方源）
ARG APT_MIRROR=
ARG PIP_MIRROR=

# 中文字体：matplotlib 图表 / PDF 中文显示
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends fonts-wqy-microhei \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-sandbox.txt /tmp/req.txt
RUN pip install --no-cache-dir ${PIP_MIRROR:+-i "$PIP_MIRROR"} -r /tmp/req.txt && rm /tmp/req.txt \
 && mkdir -p /opt/mpl \
 && printf 'font.sans-serif: WenQuanYi Micro Hei, DejaVu Sans\naxes.unicode_minus: False\n' > /opt/mpl/matplotlibrc \
 && MPLCONFIGDIR=/opt/mpl python -c "import matplotlib.font_manager as fm; fm._load_fontmanager(try_read_cache=False)" \
 && chmod -R a+rX /opt/mpl \
 && chmod 1733 /tmp

COPY runner.py /opt/runner/runner.py
RUN chmod 0700 /opt/runner && chmod 0600 /opt/runner/runner.py

EXPOSE 9000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/health').status==200 else 1)" || exit 1

CMD ["python", "/opt/runner/runner.py"]
